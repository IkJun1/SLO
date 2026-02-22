from __future__ import annotations

import shutil
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import APIError
from app.indexing import hash_text
from app.models import DocVersion, Document, SyncChange, utc_now
from app.vault import move_path, normalize_rel_path, resolve_vault_path

from .service_shared import (
    _clear_doc_chunks,
    _get_active_doc_by_path,
    _record_sync_change,
    _remap_path,
    _require_markdown_path,
    _set_doc_chunks,
)
from .sync import _load_sync_payload


def _normalize_trash_path(trash_path: str) -> str:
    normalized = normalize_rel_path(trash_path)
    if not normalized.startswith(".trash/"):
        raise APIError("INVALID_INPUT", 400, "trash_path must point to .trash subtree")
    return normalized


def _fallback_original_path_from_trash_path(trash_path: str, *, as_doc: bool) -> str:
    name = PurePosixPath(trash_path).name
    parts = name.split("-", 1)
    stripped = parts[1] if len(parts) == 2 and len(parts[0]) == 8 else name

    if as_doc and not stripped.endswith(".md"):
        stripped = f"{stripped}.md"

    return stripped


def _find_latest_deleted_doc_original_path(db: Session, doc_id: str) -> str | None:
    stmt = (
        select(SyncChange)
        .where(SyncChange.resource == "doc", SyncChange.action == "deleted", SyncChange.id == doc_id)
        .order_by(SyncChange.seq.desc())
    )
    row = db.execute(stmt).scalars().first()
    if row is None:
        return None
    return row.path


def _find_latest_deleted_folder_original_path(db: Session, trash_path: str) -> str | None:
    stmt = (
        select(SyncChange)
        .where(SyncChange.resource == "folder", SyncChange.action == "deleted")
        .order_by(SyncChange.seq.desc())
    )

    rows = db.execute(stmt).scalars().all()
    for row in rows:
        payload = _load_sync_payload(row)
        if payload.get("trashed_path") == trash_path:
            return row.path

    return None


def _candidate_restore_path(path: str, attempt: int) -> str:
    pure = PurePosixPath(path)
    parent = "" if str(pure.parent) == "." else str(pure.parent)
    suffix = pure.suffix
    stem = pure.stem if suffix else pure.name
    restored_name = f"{stem}-restored-{attempt}{suffix}"
    return f"{parent}/{restored_name}" if parent else restored_name


def _pick_available_restore_path(db: Session, settings: Settings, preferred_path: str, *, as_doc: bool) -> str:
    normalized = normalize_rel_path(preferred_path)
    if as_doc:
        _require_markdown_path(normalized)

    candidate = normalized
    attempt = 1

    while True:
        candidate_abs = resolve_vault_path(settings.vault_root, candidate)
        doc_conflict = _get_active_doc_by_path(db, candidate) is not None
        file_conflict = candidate_abs.exists()

        if not doc_conflict and not file_conflict:
            return candidate

        attempt += 1
        candidate = _candidate_restore_path(normalized, attempt)


def _hard_delete_doc_records(db: Session, settings: Settings, document: Document, *, record_sync: bool) -> None:
    trash_path = document.path
    trash_abs = resolve_vault_path(settings.vault_root, trash_path)
    if trash_abs.exists() and trash_abs.is_file():
        trash_abs.unlink()

    _clear_doc_chunks(db, settings, document.id, clear_embd=True)

    versions = db.execute(select(DocVersion).where(DocVersion.doc_id == document.id)).scalars().all()
    for version in versions:
        db.delete(version)

    db.delete(document)

    if record_sync:
        _record_sync_change(
            db,
            resource="doc",
            action="deleted",
            id_value=document.id,
            path=trash_path,
            payload={"permanent": True},
        )


def list_trash_items(db: Session, settings: Settings) -> dict[str, Any]:
    items: list[dict[str, Any]] = []

    deleted_docs = db.execute(
        select(Document).where(Document.deleted_at.is_not(None)).order_by(Document.deleted_at.desc())
    ).scalars().all()

    for doc in deleted_docs:
        original_path = _find_latest_deleted_doc_original_path(db, doc.id)
        if not original_path:
            original_path = _fallback_original_path_from_trash_path(doc.path, as_doc=True)

        items.append(
            {
                "entry_type": "doc",
                "doc_id": doc.id,
                "trash_path": doc.path,
                "original_path": original_path,
                "title": doc.title,
                "deleted_at": doc.deleted_at,
            }
        )

    deleted_folder_changes = db.execute(
        select(SyncChange)
        .where(SyncChange.resource == "folder", SyncChange.action == "deleted")
        .order_by(SyncChange.seq.desc())
    ).scalars().all()

    seen_folder_paths: set[str] = set()
    for change in deleted_folder_changes:
        payload = _load_sync_payload(change)
        trash_path_raw = payload.get("trashed_path")
        if not isinstance(trash_path_raw, str):
            continue

        try:
            trash_path = _normalize_trash_path(trash_path_raw)
        except APIError:
            continue

        if trash_path in seen_folder_paths:
            continue

        trash_abs = resolve_vault_path(settings.vault_root, trash_path)
        if not trash_abs.exists() or not trash_abs.is_dir():
            continue

        seen_folder_paths.add(trash_path)
        items.append(
            {
                "entry_type": "folder",
                "doc_id": None,
                "trash_path": trash_path,
                "original_path": change.path,
                "title": PurePosixPath(change.path).name,
                "deleted_at": change.occurred_at,
            }
        )

    items.sort(key=lambda item: item["deleted_at"], reverse=True)
    return {"items": items}


def restore_trash_entry(db: Session, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    entry_type = payload.get("entry_type")
    if entry_type not in {"doc", "folder"}:
        raise APIError("INVALID_INPUT", 400, "entry_type must be doc or folder")

    if entry_type == "doc":
        raw_doc_id = payload.get("doc_id")
        if raw_doc_id is None:
            raise APIError("INVALID_INPUT", 400, "doc_id is required for doc restore")

        doc_id = str(raw_doc_id)
        document = db.get(Document, doc_id)
        if document is None or document.deleted_at is None:
            raise APIError("NOT_FOUND", 404, "deleted document not found", {"doc_id": doc_id})

        trash_path = _normalize_trash_path(document.path)
        trash_abs = resolve_vault_path(settings.vault_root, trash_path)
        if not trash_abs.exists() or not trash_abs.is_file():
            raise APIError("NOT_FOUND", 404, "trash file not found", {"trash_path": trash_path})

        preferred_path = _find_latest_deleted_doc_original_path(db, doc_id)
        if not preferred_path:
            preferred_path = _fallback_original_path_from_trash_path(trash_path, as_doc=True)

        restore_path = _pick_available_restore_path(db, settings, preferred_path, as_doc=True)
        restore_abs = resolve_vault_path(settings.vault_root, restore_path)
        move_path(trash_abs, restore_abs)

        content = restore_abs.read_text(encoding="utf-8")
        document.path = restore_path
        document.deleted_at = None
        document.updated_at = utc_now()
        document.content_hash = hash_text(content)

        _set_doc_chunks(db, settings, document.id, content)
        _record_sync_change(
            db,
            resource="doc",
            action="updated",
            id_value=document.id,
            path=restore_path,
            payload={"restored_from": trash_path},
        )

        db.flush()
        return {"entry_type": "doc", "restored_path": restore_path, "deleted": False}

    raw_trash_path = payload.get("trash_path")
    if not isinstance(raw_trash_path, str) or raw_trash_path.strip() == "":
        raise APIError("INVALID_INPUT", 400, "trash_path is required for folder restore")

    trash_path = _normalize_trash_path(raw_trash_path)
    trash_abs = resolve_vault_path(settings.vault_root, trash_path)
    if not trash_abs.exists() or not trash_abs.is_dir():
        raise APIError("NOT_FOUND", 404, "trash folder not found", {"trash_path": trash_path})

    preferred_path = _find_latest_deleted_folder_original_path(db, trash_path)
    if not preferred_path:
        preferred_path = _fallback_original_path_from_trash_path(trash_path, as_doc=False)

    restore_path = _pick_available_restore_path(db, settings, preferred_path, as_doc=False)
    restore_abs = resolve_vault_path(settings.vault_root, restore_path)
    move_path(trash_abs, restore_abs)

    like_prefix = f"{trash_path}/%"
    trashed_docs = db.execute(
        select(Document).where(
            Document.deleted_at.is_not(None),
            or_(Document.path == trash_path, Document.path.like(like_prefix)),
        )
    ).scalars().all()

    for document in trashed_docs:
        old_trash_doc_path = document.path
        document.path = _remap_path(document.path, trash_path, restore_path)
        document.deleted_at = None
        document.updated_at = utc_now()

        restored_doc_abs = resolve_vault_path(settings.vault_root, document.path)
        if restored_doc_abs.exists() and restored_doc_abs.is_file():
            content = restored_doc_abs.read_text(encoding="utf-8")
            document.content_hash = hash_text(content)
            _set_doc_chunks(db, settings, document.id, content)

        _record_sync_change(
            db,
            resource="doc",
            action="updated",
            id_value=document.id,
            path=document.path,
            payload={"restored_from": old_trash_doc_path},
        )

    _record_sync_change(
        db,
        resource="folder",
        action="moved",
        id_value=None,
        path=restore_path,
        payload={"from_path": trash_path, "restored": True},
    )

    db.flush()
    return {"entry_type": "folder", "restored_path": restore_path, "deleted": False}


def purge_trash_entry(db: Session, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    entry_type = payload.get("entry_type")
    if entry_type not in {"doc", "folder"}:
        raise APIError("INVALID_INPUT", 400, "entry_type must be doc or folder")

    if entry_type == "doc":
        raw_doc_id = payload.get("doc_id")
        if raw_doc_id is None:
            raise APIError("INVALID_INPUT", 400, "doc_id is required for doc purge")

        doc_id = str(raw_doc_id)
        document = db.get(Document, doc_id)
        if document is None or document.deleted_at is None:
            raise APIError("NOT_FOUND", 404, "deleted document not found", {"doc_id": doc_id})

        _hard_delete_doc_records(db, settings, document, record_sync=True)
        db.flush()
        return {"entry_type": "doc", "restored_path": None, "deleted": True}

    raw_trash_path = payload.get("trash_path")
    if not isinstance(raw_trash_path, str) or raw_trash_path.strip() == "":
        raise APIError("INVALID_INPUT", 400, "trash_path is required for folder purge")

    trash_path = _normalize_trash_path(raw_trash_path)
    trash_abs = resolve_vault_path(settings.vault_root, trash_path)
    if not trash_abs.exists():
        raise APIError("NOT_FOUND", 404, "trash folder not found", {"trash_path": trash_path})

    if trash_abs.is_dir():
        shutil.rmtree(trash_abs)
    else:
        trash_abs.unlink()

    like_prefix = f"{trash_path}/%"
    trashed_docs = db.execute(
        select(Document).where(
            Document.deleted_at.is_not(None),
            or_(Document.path == trash_path, Document.path.like(like_prefix)),
        )
    ).scalars().all()

    for document in trashed_docs:
        _hard_delete_doc_records(db, settings, document, record_sync=False)

    _record_sync_change(
        db,
        resource="folder",
        action="deleted",
        id_value=None,
        path=trash_path,
        payload={"permanent": True},
    )

    db.flush()
    return {"entry_type": "folder", "restored_path": None, "deleted": True}
