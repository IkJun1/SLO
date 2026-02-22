from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import APIError
from app.indexing import hash_text, split_text_chunks
from app.models import (
    CurrentChunk,
    DocVersion,
    Document,
    EmbdChunk,
    SyncChange,
    utc_now,
)


DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120

def _ollama_openai_base(api_base: str) -> str:
    normalized = api_base.strip().rstrip("/")
    if normalized == "":
        normalized = "http://localhost:11434"
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def _synthetic_trash_path(vault_root: Path, original_name: str) -> str:
    now = datetime.now(timezone.utc)
    trash_dir = vault_root / ".trash" / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d")
    trash_dir.mkdir(parents=True, exist_ok=True)
    synthetic = trash_dir / f"{uuid4().hex[:8]}-{original_name}"
    return synthetic.relative_to(vault_root).as_posix()


def _require_markdown_path(path: str) -> None:
    if not path.endswith(".md"):
        raise APIError("INVALID_INPUT", 400, "document path must end with .md", {"path": path})


def _title_from_content(title: str | None, content: str, path: str) -> str:
    if title and title.strip():
        return title.strip()

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            derived = stripped.lstrip("#").strip()
            if derived:
                return derived

    return Path(path).stem


def _get_active_doc_by_id(db: Session, doc_id: UUID) -> Document:
    document = db.get(Document, str(doc_id))
    if document is None or document.deleted_at is not None:
        raise APIError("NOT_FOUND", 404, "document not found", {"id": str(doc_id)})
    return document


def _get_active_doc_by_path(db: Session, path: str) -> Document | None:
    stmt = select(Document).where(Document.path == path, Document.deleted_at.is_(None))
    return db.execute(stmt).scalar_one_or_none()


def _record_sync_change(
    db: Session,
    *,
    resource: str,
    action: str,
    id_value: str | None,
    path: str,
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        SyncChange(
            resource=resource,
            action=action,
            id=id_value,
            path=path,
            occurred_at=utc_now(),
            payload=json.dumps(payload or {}, ensure_ascii=True),
        )
    )


def _record_version(db: Session, *, doc_id: str, content_hash: str, content: str, reason: str | None) -> None:
    db.add(
        DocVersion(
            id=str(uuid4()),
            doc_id=doc_id,
            content_hash=content_hash,
            content=content,
            reason=reason,
            created_at=utc_now(),
        )
    )


def _chunk_id_from_index(chunk_index: int) -> str:
    return str(chunk_index)


def _chunk_start_offset(chunk_index: int) -> int:
    step = DEFAULT_CHUNK_SIZE - DEFAULT_CHUNK_OVERLAP
    return max(0, chunk_index * step)


def _clear_doc_chunks(db: Session, settings: Settings, doc_id: str, *, clear_embd: bool) -> None:
    current_chunks = db.execute(select(CurrentChunk).where(CurrentChunk.doc_id == doc_id)).scalars().all()
    for row in current_chunks:
        db.delete(row)

    if clear_embd:
        from .embeddings import _faiss_remove_ids

        embedded_chunks = db.execute(select(EmbdChunk).where(EmbdChunk.doc_id == doc_id)).scalars().all()
        _faiss_remove_ids(settings, [row.faiss_id for row in embedded_chunks if row.faiss_id is not None])
        for row in embedded_chunks:
            db.delete(row)


def _set_doc_chunks(db: Session, settings: Settings, doc_id: str, content: str) -> None:
    from .embeddings import _faiss_remove_ids

    now = utc_now()

    prepared_chunks: list[dict[str, Any]] = []
    for chunk_index, _start, _end, chunk_text in split_text_chunks(
        content,
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
    ):
        prepared_chunks.append(
            {
                "chunk_id": _chunk_id_from_index(chunk_index),
                "chunk_index": chunk_index,
                "chunk_text": chunk_text,
                "chunk_hash": hash_text(chunk_text),
            }
        )

    existing_current = db.execute(select(CurrentChunk).where(CurrentChunk.doc_id == doc_id)).scalars().all()
    current_by_chunk_id = {row.chunk_id: row for row in existing_current}

    seen_chunk_ids: set[str] = set()
    for chunk in prepared_chunks:
        chunk_id = str(chunk["chunk_id"])
        seen_chunk_ids.add(chunk_id)

        existing = current_by_chunk_id.get(chunk_id)
        if existing is None:
            db.add(
                CurrentChunk(
                    doc_id=doc_id,
                    chunk_id=chunk_id,
                    chunk_hash=str(chunk["chunk_hash"]),
                    chunk_index=int(chunk["chunk_index"]),
                    chunk_text=str(chunk["chunk_text"]),
                    updated_at=now,
                )
            )
            continue

        existing.chunk_hash = str(chunk["chunk_hash"])
        existing.chunk_index = int(chunk["chunk_index"])
        existing.chunk_text = str(chunk["chunk_text"])
        existing.updated_at = now

    for row in existing_current:
        if row.chunk_id not in seen_chunk_ids:
            db.delete(row)

    existing_embd = db.execute(select(EmbdChunk).where(EmbdChunk.doc_id == doc_id)).scalars().all()
    embd_by_chunk_id = {row.chunk_id: row for row in existing_embd}

    for chunk in prepared_chunks:
        chunk_id = str(chunk["chunk_id"])
        chunk_hash = str(chunk["chunk_hash"])
        chunk_index = int(chunk["chunk_index"])

        existing = embd_by_chunk_id.get(chunk_id)
        if existing is None:
            db.add(
                EmbdChunk(
                    doc_id=doc_id,
                    chunk_id=chunk_id,
                    chunk_hash=chunk_hash,
                    chunk_index=chunk_index,
                    state="pending",
                    updated_at=now,
                )
            )
            continue

        hash_changed = existing.chunk_hash != chunk_hash
        existing.chunk_hash = chunk_hash
        existing.chunk_index = chunk_index
        existing.updated_at = now

        if hash_changed:
            existing.state = "pending"
            existing.embedded_at = None
            existing.last_error = None

    removed_embd_rows = [row for row in existing_embd if row.chunk_id not in seen_chunk_ids]
    _faiss_remove_ids(settings, [row.faiss_id for row in removed_embd_rows if row.faiss_id is not None])
    for row in removed_embd_rows:
        db.delete(row)


def _list_docs_under_prefix(db: Session, prefix: str) -> list[Document]:
    like_prefix = f"{prefix}/%"
    stmt = select(Document).where(
        Document.deleted_at.is_(None),
        or_(Document.path == prefix, Document.path.like(like_prefix)),
    )
    return list(db.execute(stmt).scalars().all())


def _remap_path(path: str, from_prefix: str, to_prefix: str) -> str:
    if path == from_prefix:
        return to_prefix
    suffix = path[len(from_prefix) :]
    return f"{to_prefix}{suffix}"
