
import os, base64
from cryptography.fernet import Fernet

def _get_key():
    key = os.getenv("APP_SECRET_KEY", "change-me-32-bytes-min")
    b = (key.encode("utf-8") + b"0"*32)[:32]
    return base64.urlsafe_b64encode(b)

def get_fernet():
    return Fernet(_get_key())

def encrypt_text(s: str) -> str:
    return get_fernet().encrypt(s.encode("utf-8")).decode("utf-8")

def decrypt_text(s: str) -> str:
    return get_fernet().decrypt(s.encode("utf-8")).decode("utf-8")
