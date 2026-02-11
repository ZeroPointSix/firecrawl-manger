from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_AAD = b"fcam:api_key:v1"
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
    ciphertext = aes.encrypt(nonce, plaintext.encode("utf-8"), _AAD)
    return nonce + ciphertext


def decrypt_api_key(master_key: bytes, blob: bytes) -> str:
    if len(blob) < _NONCE_BYTES:
        raise ValueError("Invalid ciphertext")
    nonce = blob[:_NONCE_BYTES]
    ciphertext = blob[_NONCE_BYTES:]
    aes = AESGCM(master_key)
    plaintext = aes.decrypt(nonce, ciphertext, _AAD)
    return plaintext.decode("utf-8")


def mask_api_key_last4(api_key_last4: str) -> str:
    last4 = (api_key_last4 or "")[-4:]
    return f"fc-****{last4}" if last4 else "fc-****"

