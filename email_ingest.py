import imaplib
import email
import os
import time
from pathlib import Path

# --- CONFIG ---
IMAP_HOST = "imap.afrihost.co.za"
IMAP_USER = "dr.fs.dindar@amihealth.co.za"
IMAP_PASS = os.environ.get("AMI_EMAIL_PASSWORD")

OWNER_ID = "owner_dindar"  # hard-link this inbox to ONE doctor
POLL_SECONDS = 60

DOWNLOAD_DIR = Path("/tmp/ami_intake/dr_fs_dindar")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def connect_mailbox():
    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    mail.login(IMAP_USER, IMAP_PASS)
    mail.select("inbox")
    return mail


def process_unseen_emails(mail):
    status, messages = mail.search(None, "(UNSEEN)")
    if status != "OK":
        return

    for num in messages[0].split():
        _, data = mail.fetch(num, "(RFC822)")
        msg = email.message_from_bytes(data[0][1])

        for part in msg.walk():
            if part.get_content_type() == "application/pdf":
                filename = part.get_filename()
                if not filename:
                    continue

                file_path = DOWNLOAD_DIR / filename

                with open(file_path, "wb") as f:
                    f.write(part.get_payload(decode=True))

                print(f"[AMI-INGEST] PDF saved: {file_path}")

                # 🔗 HANDOFF POINT (next step)
                # run_worker(pdf_path=str(file_path), owner_id=OWNER_ID, source="email")

        mail.store(num, "+FLAGS", "\\Seen")


def main():
    print("[AMI-INGEST] Email ingestion started")
    while True:
        try:
            mail = connect_mailbox()
            process_unseen_emails(mail)
            mail.logout()
        except Exception as e:
            print("[AMI-INGEST] ERROR:", e)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
