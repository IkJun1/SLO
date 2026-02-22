from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import APIError
from app.models import SyncChange


def _iso_timestamp(value: datetime | None = None) -> str:
    target = value or datetime.now(timezone.utc)
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return target.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _request_meta() -> dict[str, str]:
    return {
        "request_id": f"req_{uuid4().hex[:12]}",
        "timestamp": _iso_timestamp(),
    }


def _load_sync_payload(change: SyncChange) -> dict[str, Any]:
    try:
        parsed = json.loads(change.payload) if change.payload else {}
    except json.JSONDecodeError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def get_sync_changes(
    db: Session,
    *,
    cursor: str | None,
    limit: int,
    include_deleted: bool,
) -> dict[str, Any]:
    if limit < 1 or limit > 1000:
        raise APIError("INVALID_INPUT", 400, "limit must be between 1 and 1000")

    cursor_seq = 0
    if cursor not in (None, ""):
        if not str(cursor).isdigit():
            raise APIError("INVALID_INPUT", 400, "cursor must be a positive integer string")
        cursor_seq = int(str(cursor))

    stmt = select(SyncChange).where(SyncChange.seq > cursor_seq)
    if not include_deleted:
        stmt = stmt.where(SyncChange.action != "deleted")

    rows = db.execute(stmt.order_by(SyncChange.seq.asc()).limit(limit + 1)).scalars().all()
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    changes: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row.payload) if row.payload else {}
        except json.JSONDecodeError:
            payload = {}

        changes.append(
            {
                "seq": row.seq,
                "resource": row.resource,
                "action": row.action,
                "id": row.id,
                "path": row.path,
                "occurred_at": row.occurred_at,
                "payload": payload,
            }
        )

    next_cursor = str(rows[-1].seq) if rows else str(cursor_seq)

    return {
        "data": {
            "changes": changes,
            "next_cursor": next_cursor,
            "has_more": has_more,
        },
        "meta": _request_meta(),
    }
