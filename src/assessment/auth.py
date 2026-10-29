"""Minimal HMAC-signed demo bearer tokens. Never trust a client-provided raw role."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def secret() -> bytes:
    key = os.getenv("ASSESSMENT_TOKEN_SECRET", "")
    if len(key) < 32:
        raise RuntimeError("Set ASSESSMENT_TOKEN_SECRET to a secret with at least 32 characters")
    return key.encode("utf-8")


def create_token(role: str, tenant: str = "demo", lifetime_seconds: int = 3600) -> str:
    if role not in {"admin", "viewer"} or not tenant:
        raise ValueError("Unsupported role or tenant")
    payload = {"role": role, "tenant": tenant, "exp": int(time.time()) + lifetime_seconds}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64(hmac.new(secret(), body.encode(), hashlib.sha256).digest())
    return body + "." + signature


def verify_bearer(value: str | None) -> dict | None:
    if not value or not value.startswith("Bearer "):
        return None
    token = value[7:]
    if len(token) > 4096 or token.count(".") != 1:
        return None
    body, given = token.split(".", 1)
    expected = _b64(hmac.new(secret(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(given, expected):
        return None
    try:
        claim = json.loads(_unb64(body))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(claim, dict):
        return None
    if claim.get("role") not in {"admin", "viewer"}:
        return None
    if not isinstance(claim.get("tenant"), str) or not claim["tenant"]:
        return None
    if type(claim.get("exp")) is not int or claim["exp"] <= time.time():
        return None
    return claim


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("Usage: python -m assessment.auth <admin|viewer> [tenant]")
    print(create_token(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else "demo"))
