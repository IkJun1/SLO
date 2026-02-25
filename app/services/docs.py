from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import APIError
from app.indexing import hash_text
from app.models import Document, utc_now
from app.vault import atomic_write_text, move_path, move_to_trash, normalize_rel_path, render_tree, resolve_vault_path

from .service_shared import (
    _clear_doc_chunks,
    _get_active_doc_by_id,
    _get_active_doc_by_path,
    _list_docs_under_prefix,
    _record_sync_change,
    _record_version,
    _remap_path,
    _require_markdown_path,
    _set_doc_chunks,
    _synthetic_trash_path,
    _title_from_content,
)


def get_tree_text(settings: Settings, depth: int | None, path_prefix: str) -> str:
    normalized = normalize_rel_path(path_prefix, allow_empty=True)
    base_path = resolve_vault_path(settings.vault_root, normalized, allow_empty=True)
    if not base_path.exists() or not base_path.is_dir():
        raise APIError("NOT_FOUND", 404, "folder not found", {"path": path_prefix})
    return render_tree(base_path, depth=depth)


def get_doc(db: Session, settings: Settings, path: str) -> dict[str, Any]:
    normalized_path = normalize_rel_path(path)
    document = _get_active_doc_by_path(db, normalized_path)
    if document is None:
        raise APIError("NOT_FOUND", 404, "document not found", {"path": path})
    file_path = resolve_vault_path(settings.vault_root, document.path)

    if not file_path.exists() or not file_path.is_file():
        raise APIError("NOT_FOUND", 404, "document file not found", {"path": document.path})

    content = file_path.read_text(encoding="utf-8")
    return {
        "id": document.id,
        "path": document.path,
        "title": document.title,
        "content": content,
    }


def list_docs(db: Session, path_prefix: str | None) -> dict[str, Any]:
    stmt = select(Document).where(
        Document.deleted_at.is_(None),
        Document.path != ".git",
        ~Document.path.like(".git/%"),
        ~Document.path.like("%/.git/%"),
    )

    if path_prefix not in (None, ""):
        normalized_prefix = normalize_rel_path(path_prefix)
        like_prefix = f"{normalized_prefix}/%"
        stmt = stmt.where(or_(Document.path == normalized_prefix, Document.path.like(like_prefix)))

    docs = db.execute(stmt.order_by(Document.path.asc())).scalars().all()
    return {
        "docs": [
            {
                "id": doc.id,
                "path": doc.path,
                "title": doc.title,
                "updated_at": doc.updated_at,
            }
            for doc in docs
        ]
    }


def lookup_doc_ids_by_path(db: Session, settings: Settings, raw_path: str) -> dict[str, Any]:
    normalized = normalize_rel_path(raw_path)
    like_prefix = f"{normalized}/%"

    docs = db.execute(
        select(Document)
        .where(
            Document.deleted_at.is_(None),
            or_(Document.path == normalized, Document.path.like(like_prefix)),
        )
        .order_by(Document.path.asc())
    ).scalars().all()

    if docs:
        return {"doc_ids": [doc.id for doc in docs]}

    target_abs = resolve_vault_path(settings.vault_root, normalized)
    if target_abs.exists() and target_abs.is_dir():
        return {"doc_ids": []}

    raise APIError("NOT_FOUND", 404, "path not found", {"path": raw_path})


def create_doc(db: Session, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    rel_path = normalize_rel_path(payload["path"])
    _require_markdown_path(rel_path)

    file_path = resolve_vault_path(settings.vault_root, rel_path)
    content = payload.get("content", "")

    if not isinstance(content, str):
        raise APIError("INVALID_INPUT", 400, "content must be a string")

    if not file_path.parent.exists():
        if payload.get("create_parents", False):
            file_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            raise APIError("INVALID_INPUT", 400, "parent folder does not exist", {"path": rel_path})

    if file_path.exists() and file_path.is_dir():
        raise APIError("CONFLICT", 409, "path already exists as folder", {"path": rel_path})

    existing_doc = _get_active_doc_by_path(db, rel_path)
    overwrite = bool(payload.get("overwrite", False))
    if (file_path.exists() or existing_doc is not None) and not overwrite:
        raise APIError("CONFLICT", 409, "document already exists", {"path": rel_path})

    action = "created"
    if existing_doc is None:
        existing_doc = Document(
            id=str(uuid4()),
            path=rel_path,
            title=_title_from_content(payload.get("title"), content, rel_path),
            content_hash=hash_text(content),
            updated_at=utc_now(),
            deleted_at=None,
        )
        db.add(existing_doc)
    else:
        action = "updated"
        existing_doc.path = rel_path
        existing_doc.title = _title_from_content(payload.get("title"), content, rel_path)
        existing_doc.content_hash = hash_text(content)
        existing_doc.updated_at = utc_now()
        existing_doc.deleted_at = None

    atomic_write_text(file_path, content)
    _record_version(
        db,
        doc_id=existing_doc.id,
        content_hash=existing_doc.content_hash,
        content=content,
        reason="create-doc" if action == "created" else "overwrite-doc",
    )
    _set_doc_chunks(db, settings, existing_doc.id, content)
    _record_sync_change(
        db,
        resource="doc",
        action=action,
        id_value=existing_doc.id,
        path=existing_doc.path,
        payload={"content_hash": existing_doc.content_hash},
    )

    db.flush()

    return {
        "id": existing_doc.id,
        "path": existing_doc.path,
        "title": existing_doc.title,
    }


def update_doc(db: Session, settings: Settings, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_path = normalize_rel_path(path)
    document = _get_active_doc_by_path(db, normalized_path)
    if document is None:
        raise APIError("NOT_FOUND", 404, "document not found", {"path": path})

    expected_hash = payload.get("expected_hash")
    if expected_hash and expected_hash != document.content_hash:
        raise APIError("PRECONDITION_FAILED", 412, "expected_hash mismatch", {"path": document.path})

    content = payload.get("content")
    if not isinstance(content, str):
        raise APIError("INVALID_INPUT", 400, "content must be a string")

    file_path = resolve_vault_path(settings.vault_root, document.path)
    if not file_path.exists() or not file_path.is_file():
        raise APIError("NOT_FOUND", 404, "document file not found", {"path": document.path})

    atomic_write_text(file_path, content)

    document.content_hash = hash_text(content)
    document.updated_at = utc_now()
    if payload.get("title") is not None:
        document.title = _title_from_content(payload["title"], content, document.path)

    _record_version(
        db,
        doc_id=document.id,
        content_hash=document.content_hash,
        content=content,
        reason=payload.get("reason"),
    )
    _set_doc_chunks(db, settings, document.id, content)
    _record_sync_change(
        db,
        resource="doc",
        action="updated",
        id_value=document.id,
        path=document.path,
        payload={"content_hash": document.content_hash},
    )

    db.flush()

    return {
        "id": document.id,
        "path": document.path,
        "title": document.title,
    }


def _patch_text(operation: dict[str, Any]) -> str:
    text = operation.get("text")
    if text is None or not isinstance(text, str):
        raise APIError("INVALID_INPUT", 400, "patch operation requires string text")
    return text


def _patch_target(operation: dict[str, Any]) -> str:
    target = operation.get("target")
    if target is None or not isinstance(target, str) or target == "":
        raise APIError("INVALID_INPUT", 400, "patch operation requires non-empty target")
    return target


def _apply_patch_operation(content: str, operation: dict[str, Any]) -> str:
    op = operation.get("op")

    if op == "append":
        return content + _patch_text(operation)

    if op == "prepend":
        return _patch_text(operation) + content

    if op == "replace":
        target = _patch_target(operation)
        replacement = _patch_text(operation)
        if target not in content:
            raise APIError("CONFLICT", 409, "replace target not found", {"target": target})

        count = operation.get("count")
        if count is None:
            return content.replace(target, replacement)
        return content.replace(target, replacement, int(count))

    if op in {"insert_before", "insert_after"}:
        target = _patch_target(operation)
        insert_text = _patch_text(operation)
        occurrence = operation.get("occurrence", "first")

        if occurrence not in {"first", "last"}:
            raise APIError("INVALID_INPUT", 400, "occurrence must be first or last")

        idx = content.find(target) if occurrence == "first" else content.rfind(target)
        if idx < 0:
            raise APIError("CONFLICT", 409, "insert target not found", {"target": target})

        insert_at = idx if op == "insert_before" else idx + len(target)
        return f"{content[:insert_at]}{insert_text}{content[insert_at:]}"

    raise APIError("INVALID_INPUT", 400, "unsupported patch op", {"op": op})


def apply_doc_patch(db: Session, settings: Settings, doc_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
    operations = payload.get("ops")
    if not isinstance(operations, list) or len(operations) == 0:
        raise APIError("INVALID_INPUT", 400, "ops must be a non-empty array")

    document = _get_active_doc_by_id(db, doc_id)
    file_path = resolve_vault_path(settings.vault_root, document.path)
    if not file_path.exists() or not file_path.is_file():
        raise APIError("NOT_FOUND", 404, "document file not found", {"id": str(doc_id)})

    content = file_path.read_text(encoding="utf-8")
    patched = content

    for idx, operation in enumerate(operations, start=1):
        if not isinstance(operation, dict):
            raise APIError("INVALID_INPUT", 400, "each patch op must be an object", {"op_index": idx})

        try:
            patched = _apply_patch_operation(patched, operation)
        except APIError as exc:
            details = dict(exc.details or {})
            details["op_index"] = idx
            raise APIError(exc.code, exc.status_code, exc.message, details) from exc

    update_payload: dict[str, Any] = {
        "content": patched,
        "expected_hash": payload.get("expected_hash"),
        "reason": payload.get("reason") or "apply-patch",
    }
    updated = update_doc(db, settings, document.path, update_payload)
    refreshed = _get_active_doc_by_id(db, doc_id)

    return {
        "id": updated["id"],
        "path": updated["path"],
        "title": updated["title"],
        "content_hash": refreshed.content_hash,
        "applied_ops": len(operations),
    }


def delete_doc(db: Session, settings: Settings, path: str, reason: str | None) -> dict[str, Any]:
    normalized_path = normalize_rel_path(path)
    document = _get_active_doc_by_path(db, normalized_path)
    if document is None:
        raise APIError("NOT_FOUND", 404, "document not found", {"path": path})
    old_path = document.path

    file_path = resolve_vault_path(settings.vault_root, old_path)
    if not file_path.exists() or not file_path.is_file():
        raise APIError("NOT_FOUND", 404, "document file not found", {"path": old_path})

    content = file_path.read_text(encoding="utf-8")
    trashed_rel_path = move_to_trash(settings.vault_root, file_path)

    document.path = trashed_rel_path
    document.deleted_at = utc_now()
    document.updated_at = utc_now()

    _clear_doc_chunks(db, settings, document.id, clear_embd=False)

    _record_version(
        db,
        doc_id=document.id,
        content_hash=document.content_hash,
        content=content,
        reason=reason,
    )
    _record_sync_change(
        db,
        resource="doc",
        action="deleted",
        id_value=document.id,
        path=old_path,
        payload={"reason": reason, "trashed_path": trashed_rel_path},
    )

    db.flush()
    return {"id": document.id, "path": old_path}


def move_doc(db: Session, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    document = _get_active_doc_by_id(db, payload["doc_id"])
    from_path = document.path
    to_path = normalize_rel_path(payload["to_path"])
    _require_markdown_path(to_path)

    if from_path == to_path:
        return {"id": document.id, "from_path": from_path, "to_path": to_path}

    source_abs = resolve_vault_path(settings.vault_root, from_path)
    target_abs = resolve_vault_path(settings.vault_root, to_path)

    if not source_abs.exists() or not source_abs.is_file():
        raise APIError("NOT_FOUND", 404, "source document file not found", {"path": from_path})

    if not target_abs.parent.exists():
        target_abs.parent.mkdir(parents=True, exist_ok=True)

    overwrite = bool(payload.get("overwrite", False))
    target_doc = _get_active_doc_by_path(db, to_path)
    if target_doc is not None and target_doc.id != document.id:
        if not overwrite:
            raise APIError("CONFLICT", 409, "target path already has a document", {"path": to_path})

        target_old_path = target_doc.path
        target_file = resolve_vault_path(settings.vault_root, target_doc.path)
        target_trash = _synthetic_trash_path(settings.vault_root, Path(target_doc.path).name)
        if target_file.exists() and target_file.is_file():
            target_trash = move_to_trash(settings.vault_root, target_file)

        target_doc.path = target_trash
        target_doc.deleted_at = utc_now()
        target_doc.updated_at = utc_now()

        _clear_doc_chunks(db, settings, target_doc.id, clear_embd=False)

        _record_sync_change(
            db,
            resource="doc",
            action="deleted",
            id_value=target_doc.id,
            path=target_old_path,
            payload={"reason": "overwritten-by-doc-move", "trashed_path": target_trash},
        )

    if target_abs.exists() and not overwrite:
        raise APIError("CONFLICT", 409, "target path already exists", {"path": to_path})

    if target_abs.exists() and overwrite and target_abs.is_file():
        _ = move_to_trash(settings.vault_root, target_abs)

    move_path(source_abs, target_abs)

    document.path = to_path
    document.updated_at = utc_now()
    _record_sync_change(
        db,
        resource="doc",
        action="moved",
        id_value=document.id,
        path=to_path,
        payload={"from_path": from_path},
    )

    db.flush()
    return {"id": document.id, "from_path": from_path, "to_path": to_path}


def create_folder(db: Session, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    rel_path = normalize_rel_path(payload["path"])
    folder_abs = resolve_vault_path(settings.vault_root, rel_path)

    if folder_abs.exists():
        raise APIError("CONFLICT", 409, "folder already exists", {"path": rel_path})

    if not folder_abs.parent.exists():
        if payload.get("create_parents", False):
            folder_abs.parent.mkdir(parents=True, exist_ok=True)
        else:
            raise APIError("INVALID_INPUT", 400, "parent folder does not exist", {"path": rel_path})

    folder_abs.mkdir(parents=False, exist_ok=False)
    _record_sync_change(db, resource="folder", action="created", id_value=None, path=rel_path)
    db.flush()

    return {"path": rel_path}


def delete_folder(db: Session, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    rel_path = normalize_rel_path(payload["path"])
    folder_abs = resolve_vault_path(settings.vault_root, rel_path)

    if not folder_abs.exists() or not folder_abs.is_dir():
        raise APIError("NOT_FOUND", 404, "folder not found", {"path": rel_path})

    if rel_path == "":
        raise APIError("INVALID_INPUT", 400, "cannot delete vault root")

    has_children = any(folder_abs.iterdir())
    recursive = bool(payload.get("recursive", False))
    if has_children and not recursive:
        raise APIError(
            "UNPROCESSABLE_STATE",
            422,
            "folder is not empty; set recursive=true",
            {"path": rel_path},
        )

    trashed_root = move_to_trash(settings.vault_root, folder_abs)

    docs = _list_docs_under_prefix(db, rel_path)
    for doc in docs:
        old_doc_path = doc.path
        doc.path = _remap_path(doc.path, rel_path, trashed_root)
        doc.deleted_at = utc_now()
        doc.updated_at = utc_now()

        _clear_doc_chunks(db, settings, doc.id, clear_embd=False)

        _record_sync_change(
            db,
            resource="doc",
            action="deleted",
            id_value=doc.id,
            path=old_doc_path,
            payload={"via": "folder-delete", "trashed_path": doc.path},
        )

    _record_sync_change(
        db,
        resource="folder",
        action="deleted",
        id_value=None,
        path=rel_path,
        payload={"recursive": recursive, "reason": payload.get("reason"), "trashed_path": trashed_root},
    )

    db.flush()
    return {"path": rel_path}


def move_folder(db: Session, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    from_path = normalize_rel_path(payload["from_path"])
    to_path = normalize_rel_path(payload["to_path"])

    if from_path == to_path:
        return {"from_path": from_path, "to_path": to_path}

    source_abs = resolve_vault_path(settings.vault_root, from_path)
    target_abs = resolve_vault_path(settings.vault_root, to_path)

    if not source_abs.exists() or not source_abs.is_dir():
        raise APIError("NOT_FOUND", 404, "source folder not found", {"path": from_path})

    overwrite = bool(payload.get("overwrite", False))
    if target_abs.exists() and not overwrite:
        raise APIError("CONFLICT", 409, "target path already exists", {"path": to_path})

    if target_abs.exists() and overwrite:
        moved_target = move_to_trash(settings.vault_root, target_abs)
        _record_sync_change(
            db,
            resource="folder",
            action="deleted",
            id_value=None,
            path=to_path,
            payload={"reason": "overwritten-by-folder-move", "trashed_path": moved_target},
        )
        target_docs = _list_docs_under_prefix(db, to_path)
        for doc in target_docs:
            old_doc_path = doc.path
            doc.path = _remap_path(doc.path, to_path, moved_target)
            doc.deleted_at = utc_now()
            doc.updated_at = utc_now()

            _clear_doc_chunks(db, settings, doc.id, clear_embd=False)

            _record_sync_change(
                db,
                resource="doc",
                action="deleted",
                id_value=doc.id,
                path=old_doc_path,
                payload={"reason": "overwritten-by-folder-move", "trashed_path": doc.path},
            )

    move_path(source_abs, target_abs)

    moved_docs = _list_docs_under_prefix(db, from_path)
    for doc in moved_docs:
        old_doc_path = doc.path
        doc.path = _remap_path(doc.path, from_path, to_path)
        doc.updated_at = utc_now()
        _record_sync_change(
            db,
            resource="doc",
            action="moved",
            id_value=doc.id,
            path=doc.path,
            payload={"from_path": old_doc_path},
        )

    _record_sync_change(
        db,
        resource="folder",
        action="moved",
        id_value=None,
        path=to_path,
        payload={"from_path": from_path},
    )

    db.flush()
    return {"from_path": from_path, "to_path": to_path}
