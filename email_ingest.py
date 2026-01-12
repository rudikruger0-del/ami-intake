#!/usr/bin/env python3
"""
AMI Email Ingest Service (PRODUCTION)
------------------------------------
• Poll AMI ingest mailbox via IMAP
• Extract PDF attachments
• Upload PDFs to Supabase Storage
• Insert reports with source_email
• Permanently delete email after ingestion (POPIA-safe)
• Auto-reconnect IMAP to prevent server shutdown errors
"""

import os
import imaplib
import email
import time
import uuid
from datetime import datetime
from supabase import create_client

# ==============================
# EMAIL CONFIG (AMI MAILBOX)
# ==============================
EMAIL_HOST = "fennec.aserv.co.za"
EMAIL_PORT = 993
EMAIL_USER = "ami.health.labs@amihealth.co.za"
EMAIL_PASS = os.getenv("AMI_EMAIL_PASSWORD")

# ==============================
# SUPABASE CONFIG
# ==============================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# ==============================
# TEMP DEBUG (SAFE)
# ==============================


if not all([EMAIL_PASS, SUPABASE_URL, SUPABASE_KEY]):
    raise RuntimeError("Missing required environment variables")

# ==============================
# SUPABASE CLIENT
# ==============================
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

STORAGE_BUCKET = "reports"
POLL_SECONDS = 15

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
    mail = imaplib.IMAP4_SSL(EMAIL_HOST, EMAIL_PORT)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("INBOX")
    return mail

# ==============================
# MAIN LOOP
# ==============================
def main():
    print("[AMI] Email ingest service running (PRODUCTION)")

    mail = connect_mailbox()

    while True:
        try:
            status, messages = mail.search(None, "UNSEEN")
            if status != "OK":
                time.sleep(POLL_SECONDS)
                continue

            processed_any = False

            for num in messages[0].split():
                status, data = mail.fetch(num, "(RFC822)")
                if status != "OK":
                    continue

                msg = email.message_from_bytes(data[0][1])
                source_email = extract_sender_email(msg)

                if not source_email:
                    print("⚠️ Skipping email without sender")
                    mail.store(num, "+FLAGS", "\\Deleted")
                    processed_any = True
                    continue

                for part in msg.walk():
                    if part.get_content_maintype() != "application":
                        continue

                    filename = part.get_filename()
                    if not filename or not filename.lower().endswith(".pdf"):
                        continue

                    pdf_bytes = part.get_payload(decode=True)
                    if not pdf_bytes:
                        continue

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

                    print("[AMI] Report inserted → worker will process")

                # Mark email for deletion (POPIA-safe)
                mail.store(num, "+FLAGS", "\\Deleted")
                processed_any = True
                print("[AMI] Email marked for deletion")

            # Permanently delete emails + reset IMAP
            if processed_any:
                mail.expunge()
                mail.logout()
                mail = connect_mailbox()
                print("[AMI] Mailbox expunged and reconnected")

            time.sleep(POLL_SECONDS)

        except Exception as e:
            print("❌ INGEST ERROR:", e)
            try:
                mail.logout()
            except:
                pass
            time.sleep(10)
            mail = connect_mailbox()

if __name__ == "__main__":
    main()
