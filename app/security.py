from __future__ import annotations

import hmac

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import APIError
from app.models import UserAccount
from app.services import AUTH_COOKIE_NAME, read_username_from_token


def require_user_auth(request: Request, db: Session = Depends(get_db)) -> str:
    token = str(request.cookies.get(AUTH_COOKIE_NAME, "")).strip()
    username = read_username_from_token(token)
    if username is None:
        raise APIError("UNAUTHORIZED", 401, "authentication required")

    account = db.get(UserAccount, username)
    if account is None:
        raise APIError("UNAUTHORIZED", 401, "authentication required")

    return username


def require_mcp_api_key(authorization: str | None = Header(default=None)) -> None:
    expected_key = get_settings().mcp_api_key.strip()
    if expected_key == "":
        raise APIError("MCP_API_KEY_NOT_CONFIGURED", 503, "mcp api key is not configured")

    raw_authorization = str(authorization or "").strip()
    if raw_authorization == "":
        raise APIError("UNAUTHORIZED", 401, "mcp api key is required")

    scheme, _, token = raw_authorization.partition(" ")
    candidate = token.strip()
    if scheme.lower() != "bearer" or candidate == "":
        raise APIError("UNAUTHORIZED", 401, "invalid authorization header")

    if not hmac.compare_digest(candidate, expected_key):
        raise APIError("UNAUTHORIZED", 401, "invalid mcp api key")
