#!/usr/bin/env python3
"""
AMI Email Ingest Service (FINAL)
--------------------------------
• Fetch unread emails
• Extract PDF attachments
• Upload PDFs to Supabase Storage
• Create report rows with doctor_email (no hardcoding)
• Fully compatible with email + manual workflows
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
EMAIL_HOST = "imap.gmail.com"
EMAIL_USER = "ami.health.alerts@gmail.com"
EMAIL_PASS = os.getenv("AMI_EMAIL_PASSWORD")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not all([EMAIL_PASS, SUPABASE_URL, SUPABASE_KEY]):
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
    return normalise_email(parsed)

# ==============================
# MAIN INGEST LOOP
# ==============================
def main():
    print("[AMI] Email ingest service running (FINAL)")

    mail = imaplib.IMAP4_SSL(EMAIL_HOST)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("inbox")

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
                doctor_email = extract_sender_email(msg)

                if not doctor_email:
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
                        f"incoming/{doctor_email}/"
                        f"{utc_stamp()}_{filename}"
                    )

                    print(f"[AMI] Uploading to storage: {storage_path}")

                    supabase.storage.from_(STORAGE_BUCKET).upload(
                        storage_path,
                        pdf_bytes,
                        {"content-type": "application/pdf"}
                    )

                    # Create report record (doctor_id intentionally NULL)
                    supabase.table("reports").insert({
                        "doctor_email": doctor_email,
                        "doctor_id": None,
                        "file_path": storage_path,
                        "ai_status": "pending",
                        "ingest_source": "email"
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

