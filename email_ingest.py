#!/usr/bin/env python3
"""
AMI Email Ingest Service (PRODUCTION – HARDENED)
------------------------------------------------
• Poll AMI ingest mailbox via IMAP
• Extract ALL PDF attachments (supports multiple PDFs per email)
• Upload PDFs to Supabase Storage
• Insert reports with source_email
• Delete email ONLY after successful ingestion
• Reconnect IMAP every poll (no long-lived sockets)
• Backoff on failures (prevents hammering + bans)
"""

import os
import imaplib
import email
import time
import uuid
import socket
from datetime import datetime
from supabase import create_client

# ==============================
# CONFIG
# ==============================
EMAIL_HOST = "fennec.aserv.co.za"
EMAIL_PORT = 993
EMAIL_USER = "ami.health.labs@amihealth.co.za"
EMAIL_PASS = os.getenv("AMI_EMAIL_PASSWORD")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not all([EMAIL_PASS, SUPABASE_URL, SUPABASE_KEY]):
    raise RuntimeError("Missing required environment variables")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

STORAGE_BUCKET = "reports"

POLL_SECONDS = 15
FAIL_BACKOFF_SECONDS = 30

# ==============================
# HELPERS
# ==============================
def utc_stamp():
    return datetime.utcnow().strftime("%Y%m%dT%H%M%S")

def normalise_email(addr: str) -> str:
    return addr.strip().lower()

def extract_sender_email(msg):
    frm = msg.get("From", "")
    parsed = email.utils.parseaddr(frm)[1]
    return normalise_email(parsed)

def connect_mailbox():
    # Short socket timeout prevents hangs
    socket.setdefaulttimeout(20)

    mail = imaplib.IMAP4_SSL(EMAIL_HOST, EMAIL_PORT)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("INBOX")
    return mail

# ==============================
# MAIN LOOP
# ==============================
def main():
    print("[AMI] Email ingest service running (PRODUCTION)")

    while True:
        mail = None
        try:
            mail = connect_mailbox()

            status, messages = mail.search(None, "UNSEEN")
            if status != "OK":
                mail.logout()
                time.sleep(POLL_SECONDS)
                continue

            msg_ids = messages[0].split()

            for num in msg_ids:
                status, data = mail.fetch(num, "(RFC822)")
                if status != "OK":
                    continue

                msg = email.message_from_bytes(data[0][1])
                source_email = extract_sender_email(msg)

                if not source_email:
                    mail.store(num, "+FLAGS", "\\Deleted")
                    continue

                pdf_found = False

                for part in msg.walk():
                    if part.get_content_maintype() != "application":
                        continue

                    filename = part.get_filename()
                    if not filename or not filename.lower().endswith(".pdf"):
                        continue

                    pdf_bytes = part.get_payload(decode=True)
                    if not pdf_bytes:
                        continue

                    pdf_found = True

                    storage_path = (
                        f"incoming/{source_email}/"
                        f"{utc_stamp()}_{uuid.uuid4()}_{filename}"
                    )

                    print(f"[AMI] Uploading PDF → {storage_path}")

                    supabase.storage.from_(STORAGE_BUCKET).upload(
                        storage_path,
                        pdf_bytes,
                        {"content-type": "application/pdf"},
                    )

                    supabase.table("reports").insert({
                        "file_path": storage_path,
                        "ai_status": "pending",
                        "ingest_source": "email",
                        "source_email": source_email,
                        "user_id": None,
                    }).execute()

                # Delete ONLY if we processed at least one PDF
                if pdf_found:
                    mail.store(num, "+FLAGS", "\\Deleted")
                    print("[AMI] Email processed & marked for deletion")

            # Permanently remove deleted emails
            mail.expunge()
            mail.logout()

            time.sleep(POLL_SECONDS)

        except Exception as e:
            print("❌ INGEST ERROR:", e)

            try:
                if mail:
                    mail.logout()
            except:
                pass

            # Backoff prevents server bans + runaway loops
            time.sleep(FAIL_BACKOFF_SECONDS)

# ==============================
if __name__ == "__main__":
    main()
