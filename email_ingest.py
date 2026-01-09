import imaplib
import os
import time

IMAP_HOST = "imap.afrihost.co.za"
IMAP_USER = "dr.fs.dindar@amihealth.co.za"
IMAP_PASS = os.environ.get("F.S Dindar")

POLL_SECONDS = 60

def check_for_new_email():
    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    mail.login(IMAP_USER, IMAP_PASS)
    mail.select("inbox")

    status, messages = mail.search(None, "(UNSEEN)")
    count = len(messages[0].split())

    if count > 0:
        print("[AMI] New email detected for Dr Dindar")

    mail.logout()

def main():
    print("[AMI] Intake service running (acknowledge-only)")
    while True:
        check_for_new_email()
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()

