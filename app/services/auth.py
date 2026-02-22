from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from hmac import compare_digest
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import APIError
from app.models import UserAccount


AUTH_COOKIE_NAME = "slo_auth_token"
AUTH_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30

_auth_cookie_secret = secrets.token_bytes(32)


def _auth_b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _auth_b64_decode(value: str) -> bytes:
    padding = "=" * ((4 - (len(value) % 4)) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def issue_auth_token(username: str) -> str:
    normalized = username.strip()
    now = int(time.time())
    payload: dict[str, object] = {
        "u": normalized,
        "iat": now,
        "exp": now + AUTH_COOKIE_MAX_AGE_SECONDS,
    }
    payload_raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    payload_segment = _auth_b64_encode(payload_raw)
    signature_raw = hmac.new(_auth_cookie_secret, payload_segment.encode("ascii"), hashlib.sha256).digest()
    signature_segment = _auth_b64_encode(signature_raw)
    return f"{payload_segment}.{signature_segment}"


def read_username_from_token(token: str) -> str | None:
    value = token.strip()
    if value == "" or "." not in value:
        return None

    payload_segment, signature_segment = value.split(".", 1)
    if payload_segment == "" or signature_segment == "":
        return None

    expected_signature_raw = hmac.new(
        _auth_cookie_secret,
        payload_segment.encode("ascii"),
        hashlib.sha256,
    ).digest()
    expected_signature_segment = _auth_b64_encode(expected_signature_raw)
    if not compare_digest(expected_signature_segment, signature_segment):
        return None

    try:
        payload_raw = json.loads(_auth_b64_decode(payload_segment).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None

    if not isinstance(payload_raw, dict):
        return None

    username_raw = payload_raw.get("u")
    if not isinstance(username_raw, str):
        return None
    username = username_raw.strip()
    if username == "":
        return None

    exp_raw = payload_raw.get("exp")
    if isinstance(exp_raw, int):
        exp = exp_raw
    elif isinstance(exp_raw, str):
        exp_text = exp_raw.strip()
        if exp_text == "" or not exp_text.isdigit():
            return None
        exp = int(exp_text)
    else:
        return None

    if exp < int(time.time()):
        return None

    return username


def _normalize_username(raw: Any) -> str:
    return str(raw).strip()


def _hash_password_with_salt(password: str, salt: str) -> str:
    raw = f"{password}{salt}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def has_registered_user(db: Session) -> bool:
    count = db.execute(select(func.count()).select_from(UserAccount)).scalar_one()
    return int(count) > 0


def get_auth_status(db: Session) -> dict[str, Any]:
    return {"has_users": has_registered_user(db)}


def signup_user(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    username = _normalize_username(payload.get("username", ""))
    password = str(payload.get("password", ""))

    if username == "":
        raise APIError("INVALID_INPUT", 400, "username is required")
    if password == "":
        raise APIError("INVALID_INPUT", 400, "password is required")

    if has_registered_user(db):
        raise APIError("CONFLICT", 409, "signup is disabled after initial account setup")

    existing = db.get(UserAccount, username)
    if existing is not None:
        raise APIError("CONFLICT", 409, "username already exists", {"username": username})

    salt = secrets.token_hex(16)
    password_hash = _hash_password_with_salt(password, salt)
    account = UserAccount(username=username, password_hash=password_hash, salt=salt)
    db.add(account)
    db.flush()

    return {"username": account.username}


def login_user(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    username = _normalize_username(payload.get("username", ""))
    password = str(payload.get("password", ""))

    if username == "":
        raise APIError("INVALID_INPUT", 400, "username is required")
    if password == "":
        raise APIError("INVALID_INPUT", 400, "password is required")

    account = db.get(UserAccount, username)
    if account is None:
        raise APIError("INVALID_CREDENTIALS", 401, "invalid username or password")

    expected_hash = _hash_password_with_salt(password, account.salt)
    if not compare_digest(expected_hash, account.password_hash):
        raise APIError("INVALID_CREDENTIALS", 401, "invalid username or password")

    return {"authenticated": True, "username": account.username}
