#!/usr/bin/env python3
"""
AMI Email Ingest Service
------------------------
• Fetch unread emails via IMAP (provider-agnostic)
• Extract PDF attachments
• Upload PDFs to Supabase Storage
• Create report rows with user_id = NULL
• Ownership resolved automatically on login
"""

import os
import imaplib
import email
import time
from datetime import datetime
from supabase import create_client, Client

# ==============================
# ENVIRONMENT
# ==============================
IMAP_HOST = os.getenv("IMAP_HOST")
IMAP_USER = os.getenv("IMAP_USER")
IMAP_PASS = os.getenv("IMAP_PASS")
IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not all([IMAP_HOST, IMAP_USER, IMAP_PASS, SUPABASE_URL, SUPABASE_KEY]):
    raise RuntimeError("Missing required environment variables")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

STORAGE_BUCKET = "reports"
POLL_SECONDS = 10

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
    return normalise_email(parsed) if parsed else None

# ==============================
# MAIN INGEST LOOP
# ==============================
def main():
    print("[AMI] Email ingest service running")

    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    mail.login(IMAP_USER, IMAP_PASS)
    mail.select(IMAP_FOLDER)

    while True:
        try:
            status, messages = mail.search(None, "UNSEEN")
            if status != "OK":
                time.sleep(POLL_SECONDS)
                continue

            for num in messages[0].split():
                status, data = mail.fetch(num, "(RFC822)")
                if status != "OK":
                    continue

                msg = email.message_from_bytes(data[0][1])
                sender_email = extract_sender_email(msg)

                if not sender_email:
                    print("⚠️ Skipping email without sender")
                    mail.store(num, "+FLAGS", "\\Seen")
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
                        f"email/{sender_email}/"
                        f"{utc_stamp()}_{filename}"
                    )

                    print(f"[AMI] Uploading PDF → {storage_path}")

                    supabase.storage.from_(STORAGE_BUCKET).upload(
                        storage_path,
                        pdf_bytes,
                        {"content-type": "application/pdf"}
                    )

                    # IMPORTANT: user_id intentionally NULL
                    supabase.table("reports").insert({
                        "user_id": None,
                        "source": "email",
                        "source_email": sender_email,
                        "file_path": storage_path,
                        "ai_status": "pending",
                        "received_at": datetime.utcnow().isoformat()
                    }).execute()

                    print("[AMI] Report queued → worker will process")

                mail.store(num, "+FLAGS", "\\Seen")
                print("[AMI] Email processed and marked as SEEN")

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            print("\n[AMI] Shutting down ingest service")
            break
        except Exception as e:
            print("❌ INGEST ERROR:", e)
            time.sleep(5)

if __name__ == "__main__":
    main()

