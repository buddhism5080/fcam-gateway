from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_AAD_API_KEY = b"fcam:api_key:v1"
_AAD_ACCOUNT_PASSWORD = b"fcam:account_password:v1"
_NONCE_BYTES = 12


def derive_master_key_bytes(master_key: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(master_key)
        if len(raw) == 32:
            return raw
    except Exception:
        pass
    return hashlib.sha256(master_key.encode("utf-8")).digest()


def hmac_sha256_hex(key: bytes, message: str) -> str:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def encrypt_api_key(master_key: bytes, plaintext: str) -> bytes:
    aes = AESGCM(master_key)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = aes.encrypt(nonce, plaintext.encode("utf-8"), _AAD_API_KEY)
    return nonce + ciphertext


def decrypt_api_key(master_key: bytes, blob: bytes) -> str:
    if len(blob) < _NONCE_BYTES:
        raise ValueError("Invalid ciphertext")
    nonce = blob[:_NONCE_BYTES]
    ciphertext = blob[_NONCE_BYTES:]
    aes = AESGCM(master_key)
    plaintext = aes.decrypt(nonce, ciphertext, _AAD_API_KEY)
    return plaintext.decode("utf-8")


def encrypt_account_password(master_key: bytes, plaintext: str) -> bytes:
    aes = AESGCM(master_key)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = aes.encrypt(nonce, plaintext.encode("utf-8"), _AAD_ACCOUNT_PASSWORD)
    return nonce + ciphertext


def decrypt_account_password(master_key: bytes, blob: bytes) -> str:
    if len(blob) < _NONCE_BYTES:
        raise ValueError("Invalid ciphertext")
    nonce = blob[:_NONCE_BYTES]
    ciphertext = blob[_NONCE_BYTES:]
    aes = AESGCM(master_key)
    plaintext = aes.decrypt(nonce, ciphertext, _AAD_ACCOUNT_PASSWORD)
    return plaintext.decode("utf-8")


def mask_api_key_last4(api_key_last4: str) -> str:
    last4 = (api_key_last4 or "")[-4:]
    return f"fc-****{last4}" if last4 else "fc-****"

_AAD_CLIENT_TOKEN = b"fcam:client_token:v1"


def encrypt_client_token(master_key: bytes, plaintext: str) -> bytes:
    aes = AESGCM(master_key)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = aes.encrypt(nonce, plaintext.encode("utf-8"), _AAD_CLIENT_TOKEN)
    return nonce + ciphertext


def decrypt_client_token(master_key: bytes, blob: bytes) -> str:
    if len(blob) < _NONCE_BYTES:
        raise ValueError("Invalid ciphertext")
    nonce = blob[:_NONCE_BYTES]
    ciphertext = blob[_NONCE_BYTES:]
    aes = AESGCM(master_key)
    plaintext = aes.decrypt(nonce, ciphertext, _AAD_CLIENT_TOKEN)
    return plaintext.decode("utf-8")


def generate_client_token() -> str:
    """Cryptographically strong client token."""
    import secrets as _secrets

    return f"fcam_client_{_secrets.token_urlsafe(32)}"


def validate_client_token_complexity(token: str) -> None:
    """
    Manual client tokens must meet minimum complexity.
    Raises ValueError with a human-readable message on failure.
    """
    t = (token or "").strip()
    if len(t) < 24:
        raise ValueError("令牌长度至少 24 个字符")
    if len(t) > 256:
        raise ValueError("令牌长度不能超过 256 个字符")
    classes = 0
    if any(c.islower() for c in t):
        classes += 1
    if any(c.isupper() for c in t):
        classes += 1
    if any(c.isdigit() for c in t):
        classes += 1
    if any(not c.isalnum() for c in t):
        classes += 1
    if classes < 3:
        raise ValueError("令牌需至少包含三类字符（大写/小写/数字/特殊符号）")
    # reject trivial patterns
    low = t.lower()
    if low in {"password", "admin", "fcam_client_test"} or low == "fcam_client_" + ("a" * 20):
        raise ValueError("令牌过于简单")

