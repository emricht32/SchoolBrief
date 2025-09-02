# scripts/test_smtp.py
import os, smtplib, ssl
from email.message import EmailMessage
from dotenv import load_dotenv
load_dotenv()

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
FROM = os.getenv("SMTP_FROM_EMAIL", SMTP_USERNAME)
TO = os.getenv("SMTP_TEST_TO", SMTP_USERNAME)

msg = EmailMessage()
msg["Subject"] = "SMTP test"
msg["From"] = FROM
msg["To"] = TO
msg.set_content("This is a test.")

ctx = ssl.create_default_context()
with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
    s.ehlo()
    s.starttls(context=ctx)
    s.ehlo()
    s.login(SMTP_USERNAME, SMTP_PASSWORD)
    s.send_message(msg)
print("Sent OK")

