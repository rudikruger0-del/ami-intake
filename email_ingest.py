#!/usr/bin/env python3
"""
AMI Email Ingest Service (PRODUCTION)
------------------------------------
• Poll AMI ingest mailbox via IMAP (stateless, reconnect every cycle)
• Extract ALL PDF attachments (handles forwarded emails)
• Upload PDFs to Supabase Storage
• Insert reports with source_email
• Permanently delete emails after successful ingestion (POPIA-safe)
• Resilient to IMAP timeouts, server restarts, and network issues
"""

import os
import imaplib
import email
import time
import uuid
import socket
from datetime import datetime, timezone
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

STORAGE_BUCKET = "reports"
POLL_SECONDS = 20
MAX_BACKOFF = 300  # 5 minutes max backoff

if not all([EMAIL_PASS, SUPABASE_URL, SUPABASE_KEY]):
    raise RuntimeError("Missing required environment variables")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==============================
# HELPERS
# ==============================
def utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

def normalise_email(addr: str) -> str:
    return addr.strip().lower()

def extract_sender_email(msg):
    hdr = msg.get("From", "")
    parsed = email.utils.parseaddr(hdr)[1]
    return normalise_email(parsed) if parsed else "unknown_sender"

def connect_mailbox():
    mail = imaplib.IMAP4_SSL(EMAIL_HOST, EMAIL_PORT, timeout=30)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("INBOX")
    return mail

# ==============================
# MAIN LOOP
# ==============================
def main():
    print("[AMI] Email ingest service running (PRODUCTION)")

    backoff = POLL_SECONDS

    while True:
        mail = None
        try:
            mail = connect_mailbox()

            # SEARCH ALL — not UNSEEN (forwarded mails often break UNSEEN)
            status, messages = mail.search(None, "ALL")
            if status != "OK":
                raise RuntimeError("IMAP SEARCH failed")

            message_ids = messages[0].split()
            if not message_ids:
                backoff = POLL_SECONDS
                time.sleep(POLL_SECONDS)
                continue

            for msg_id in message_ids:
                status, data = mail.fetch(msg_id, "(RFC822)")
                if status != "OK" or not data:
                    continue

                msg = email.message_from_bytes(data[0][1])
                source_email = extract_sender_email(msg)

                pdf_count = 0

                for part in msg.walk():
                    content_type = part.get_content_type()
                    filename = part.get_filename()

                    if (
                        content_type == "application/pdf"
                        or (filename and filename.lower().endswith(".pdf"))
                    ):
                        pdf_bytes = part.get_payload(decode=True)
                        if not pdf_bytes:
                            continue

                        pdf_count += 1
                        storage_path = (
                            f"incoming/{source_email}/"
                            f"{utc_stamp()}_{uuid.uuid4()}_{filename or 'report.pdf'}"
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

                if pdf_count > 0:
                    mail.store(msg_id, "+FLAGS", "\\Deleted")
                    print(f"[AMI] Email processed ({pdf_count} PDFs) & marked for deletion")
                else:
                    print("[AMI] No PDFs found — leaving email untouched")

            # PERMANENT DELETE (POPIA)
            mail.expunge()
            backoff = POLL_SECONDS

        except (
            imaplib.IMAP4.error,
            socket.timeout,
            TimeoutError,
            ConnectionError,
            OSError,
        ) as e:
            print("❌ INGEST ERROR:", e)
            backoff = min(backoff * 2, MAX_BACKOFF)

        except Exception as e:
            print("❌ UNEXPECTED ERROR:", e)
            backoff = min(backoff * 2, MAX_BACKOFF)

        finally:
            try:
                if mail:
                    mail.logout()
            except Exception:
                pass

            time.sleep(backoff)

if __name__ == "__main__":
    main()
