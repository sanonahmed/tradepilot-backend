import os
import base64
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from passlib.context import CryptContext
from jose import jwt, JWTError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# --- Password Hashing ---
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# --- JWT Tokens ---
def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "iat": now, "type": "access"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "iat": now, "type": "refresh"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None


# --- AES-256-GCM Encryption for Binance API Secrets ---
def _get_encryption_key() -> bytes:
    key_bytes = settings.ENCRYPTION_KEY.encode("utf-8")
    if len(key_bytes) < 32:
        key_bytes = key_bytes.ljust(32, b"0")
    return key_bytes[:32]


def encrypt_secret(plain_text: str) -> str:
    """Encrypts sensitive credentials with AES-256-GCM."""
    if not plain_text:
        return ""
    key = _get_encryption_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plain_text.encode("utf-8"), None)
    # Store as base64 of nonce + ciphertext
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode("utf-8")


def decrypt_secret(cipher_text: str) -> str:
    """Decrypts ciphertext with AES-256-GCM."""
    if not cipher_text:
        return ""
    try:
        key = _get_encryption_key()
        aesgcm = AESGCM(key)
        combined = base64.b64decode(cipher_text.encode("utf-8"))
        nonce = combined[:12]
        encrypted = combined[12:]
        decrypted = aesgcm.decrypt(nonce, encrypted, None)
        return decrypted.decode("utf-8")
    except Exception as e:
        raise ValueError("Failed to decrypt sensitive secret") from e


def mask_secret(secret: str, visible_start: int = 4, visible_end: int = 4) -> str:
    """Safe display mask for API keys in UI responses."""
    if not secret:
        return ""
    if len(secret) <= (visible_start + visible_end):
        return "***"
    return f"{secret[:visible_start]}...{secret[-visible_end:]}"
