from __future__ import annotations

import base64

import pytest

from app.core.security import (
    constant_time_equals,
    decrypt_api_key,
    derive_master_key_bytes,
    encrypt_api_key,
    hmac_sha256_hex,
    mask_api_key_last4,
)

pytestmark = pytest.mark.unit


def test_derive_master_key_bytes_accepts_base64_32_bytes():
    raw = b"a" * 32
    b64 = base64.urlsafe_b64encode(raw).decode("ascii")
    out = derive_master_key_bytes(b64)
    assert out == raw


def test_encrypt_decrypt_roundtrip():
    key = derive_master_key_bytes("master")
    blob = encrypt_api_key(key, "fc-1234567890abcdef")
    plain = decrypt_api_key(key, blob)
    assert plain == "fc-1234567890abcdef"


def test_hmac_sha256_hex_and_constant_time_equals():
    key = derive_master_key_bytes("master")
    a = hmac_sha256_hex(key, "token")
    b = hmac_sha256_hex(key, "token")
    assert constant_time_equals(a, b) is True


def test_mask_api_key_last4():
    assert mask_api_key_last4("5678") == "fc-****5678"

