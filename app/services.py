from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from hmac import compare_digest
from pathlib import Path, PurePosixPath
from textwrap import dedent
from typing import Any, Sequence
from urllib.parse import quote
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import APIError
from app.indexing import hash_text, keyword_score, make_snippet, split_text_chunks, tokenize
from app.models import (
    ChatMessage,
    ChatSession,
    CurrentChunk,
    DocVersion,
    Document,
    EmbdChunk,
    SyncChange,
    UserAccount,
    utc_now,
)
from app.vault import (
    atomic_write_bytes,
    atomic_write_text,
    move_path,
    move_to_trash,
    normalize_rel_path,
    render_tree,
    resolve_vault_path,
)

try:
    import faiss  # pyright: ignore[reportMissingImports]
except ImportError:
    faiss = None  # type: ignore[assignment]

try:
    import numpy as np  # pyright: ignore[reportMissingImports]
except ImportError:
    np = None  # type: ignore[assignment]


DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120

_embedding_sync_lock = threading.Lock()
_faiss_io_lock = threading.Lock()
_embedding_last_run_at: datetime | None = None


def _ollama_openai_base(api_base: str) -> str:
    normalized = api_base.strip().rstrip("/")
    if normalized == "":
        normalized = "http://localhost:11434"
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def _faiss_runtime_available() -> bool:
    return faiss is not None and np is not None


def _require_faiss_runtime() -> tuple[Any, Any]:
    if faiss is None or np is None:
        raise APIError("INDEXING_UNAVAILABLE", 503, "faiss dependencies are not installed")
    return faiss, np


def _load_faiss_index_unlocked(settings: Settings, *, dimension: int | None) -> Any | None:
    lib, _np = _require_faiss_runtime()
    index_path = settings.faiss_index_path

    if index_path.exists():
        try:
            index = lib.read_index(str(index_path))
        except Exception as exc:
            raise APIError(
                "INDEXING_UNAVAILABLE",
                503,
                "failed to load faiss index",
                {"path": str(index_path), "reason": str(exc)[:2000]},
            ) from exc

        if dimension is not None and int(index.d) != int(dimension):
            raise APIError(
                "INDEXING_UNAVAILABLE",
                503,
                "faiss index dimension mismatch",
                {
                    "path": str(index_path),
                    "index_dim": int(index.d),
                    "expected_dim": int(dimension),
                },
            )
        return index

    if dimension is None:
        return None

    base = lib.IndexFlatIP(int(dimension))
    return lib.IndexIDMap2(base)


def _save_faiss_index_unlocked(settings: Settings, index: Any) -> None:
    lib, _np = _require_faiss_runtime()
    index_path = settings.faiss_index_path

    fd, temp_path = tempfile.mkstemp(
        dir=str(index_path.parent),
        prefix=".faiss_tmp_",
        suffix=".index",
    )
    os.close(fd)

    try:
        lib.write_index(index, temp_path)
        os.replace(temp_path, str(index_path))
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _faiss_remove_ids(settings: Settings, faiss_ids: list[int]) -> int:
    unique_ids: set[int] = set()
    for item in faiss_ids:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            unique_ids.add(parsed)

    sorted_ids = sorted(unique_ids)
    if not sorted_ids:
        return 0

    if not _faiss_runtime_available():
        return 0

    _lib, np_mod = _require_faiss_runtime()
    with _faiss_io_lock:
        index = _load_faiss_index_unlocked(settings, dimension=None)
        if index is None:
            return 0

        id_array = np_mod.asarray(sorted_ids, dtype=np_mod.int64)
        try:
            removed = int(index.remove_ids(id_array))
        except Exception:
            return 0

        if removed > 0:
            try:
                _save_faiss_index_unlocked(settings, index)
            except APIError:
                return 0
        return removed


def _faiss_upsert_vectors(settings: Settings, records: list[tuple[int, list[float]]], *, dimension: int) -> None:
    if not records:
        return

    _lib, np_mod = _require_faiss_runtime()

    ids: list[int] = []
    vectors: list[list[float]] = []
    for faiss_id, vector in records:
        ids.append(int(faiss_id))
        vectors.append([float(value) for value in vector])

    vector_array = np_mod.asarray(vectors, dtype=np_mod.float32)
    if vector_array.ndim != 2 or int(vector_array.shape[1]) != int(dimension):
        raise APIError(
            "INDEXING_UNAVAILABLE",
            503,
            "faiss vector dimension mismatch",
            {
                "expected_dim": int(dimension),
                "actual_shape": list(vector_array.shape),
            },
        )

    id_array = np_mod.asarray(ids, dtype=np_mod.int64)

    with _faiss_io_lock:
        index = _load_faiss_index_unlocked(settings, dimension=dimension)
        if index is None:
            raise APIError("INDEXING_UNAVAILABLE", 503, "failed to create faiss index")

        try:
            _ = index.remove_ids(id_array)
            index.add_with_ids(vector_array, id_array)
        except Exception as exc:
            raise APIError(
                "INDEXING_UNAVAILABLE",
                503,
                "failed to upsert vectors into faiss",
                {"reason": str(exc)[:2000]},
            ) from exc

        _save_faiss_index_unlocked(settings, index)


def _iso_timestamp(value: datetime | None = None) -> str:
    actual = value or utc_now()
    if actual.tzinfo is None:
        actual = actual.replace(tzinfo=timezone.utc)
    return actual.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _request_meta() -> dict[str, str]:
    return {
        "request_id": f"req_{uuid4().hex[:12]}",
        "timestamp": _iso_timestamp(),
    }


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
        embedded_chunks = db.execute(select(EmbdChunk).where(EmbdChunk.doc_id == doc_id)).scalars().all()
        _faiss_remove_ids(settings, [row.faiss_id for row in embedded_chunks if row.faiss_id is not None])
        for row in embedded_chunks:
            db.delete(row)


def _set_doc_chunks(db: Session, settings: Settings, doc_id: str, content: str) -> None:
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


def bootstrap_documents_from_vault(db: Session, settings: Settings) -> dict[str, int]:
    active_docs = db.execute(select(Document).where(Document.deleted_at.is_(None))).scalars().all()
    docs_by_path = {doc.path: doc for doc in active_docs}

    created_docs = 0
    refreshed_docs = 0

    for file_path in sorted(settings.vault_root.rglob("*.md")):
        if not file_path.is_file():
            continue

        rel_path = file_path.relative_to(settings.vault_root).as_posix()
        if rel_path.startswith(".trash/"):
            continue

        content = file_path.read_text(encoding="utf-8")
        content_hash = hash_text(content)
        existing = docs_by_path.get(rel_path)

        if existing is None:
            document = Document(
                id=str(uuid4()),
                path=rel_path,
                title=_title_from_content(None, content, rel_path),
                content_hash=content_hash,
                updated_at=utc_now(),
                deleted_at=None,
            )
            db.add(document)
            docs_by_path[rel_path] = document
            created_docs += 1
        else:
            document = existing
            document.title = _title_from_content(document.title, content, rel_path)

        has_current_rows = (
            db.execute(select(CurrentChunk.doc_id).where(CurrentChunk.doc_id == document.id).limit(1)).scalar_one_or_none()
            is not None
        )

        if document.content_hash == content_hash and has_current_rows:
            continue

        document.content_hash = content_hash
        document.updated_at = utc_now()
        _set_doc_chunks(db, settings, document.id, content)
        refreshed_docs += 1

    db.flush()
    return {
        "created_docs": created_docs,
        "refreshed_docs": refreshed_docs,
    }


def _refresh_current_chunks_from_documents(db: Session, settings: Settings) -> dict[str, int]:
    docs = db.execute(select(Document).where(Document.deleted_at.is_(None)).order_by(Document.path.asc())).scalars().all()

    scanned_docs = len(docs)
    refreshed_docs = 0
    missing_docs = 0

    for document in docs:
        file_path = resolve_vault_path(settings.vault_root, document.path)
        if not file_path.exists() or not file_path.is_file():
            missing_docs += 1
            continue

        content = file_path.read_text(encoding="utf-8")
        content_hash = hash_text(content)
        has_current_rows = (
            db.execute(select(CurrentChunk.doc_id).where(CurrentChunk.doc_id == document.id).limit(1)).scalar_one_or_none()
            is not None
        )

        if content_hash == document.content_hash and has_current_rows:
            continue

        document.content_hash = content_hash
        document.updated_at = utc_now()
        _set_doc_chunks(db, settings, document.id, content)
        refreshed_docs += 1

    db.flush()
    return {
        "scanned_docs": scanned_docs,
        "refreshed_docs": refreshed_docs,
        "missing_docs": missing_docs,
    }


def _reconcile_embd_table(db: Session, settings: Settings) -> dict[str, int]:
    current_rows = db.execute(select(CurrentChunk)).scalars().all()
    embd_rows = db.execute(select(EmbdChunk)).scalars().all()

    current_by_key = {(row.doc_id, row.chunk_id): row for row in current_rows}
    embd_by_key = {(row.doc_id, row.chunk_id): row for row in embd_rows}

    created = 0
    updated = 0
    deleted = 0
    now = utc_now()

    for key, current in current_by_key.items():
        existing = embd_by_key.get(key)
        if existing is None:
            db.add(
                EmbdChunk(
                    doc_id=current.doc_id,
                    chunk_id=current.chunk_id,
                    chunk_hash=current.chunk_hash,
                    chunk_index=current.chunk_index,
                    state="pending",
                    updated_at=now,
                )
            )
            created += 1
            continue

        stale = False
        if existing.chunk_hash != current.chunk_hash:
            existing.chunk_hash = current.chunk_hash
            stale = True

        if existing.chunk_index != current.chunk_index:
            existing.chunk_index = current.chunk_index
            stale = True

        if (
            settings.embedding_provider not in {"", "none"}
            and existing.embedding_model not in {"", settings.embedding_model}
        ):
            stale = True

        if stale:
            existing.state = "pending"
            existing.embedded_at = None
            existing.last_error = None
            updated += 1

        existing.updated_at = now

    orphan_rows: list[EmbdChunk] = []
    for key, embd in embd_by_key.items():
        if key not in current_by_key:
            orphan_rows.append(embd)

    if orphan_rows:
        _faiss_remove_ids(settings, [row.faiss_id for row in orphan_rows if row.faiss_id is not None])
        for row in orphan_rows:
            db.delete(row)
            deleted += 1

    db.flush()
    return {
        "created": created,
        "updated": updated,
        "deleted": deleted,
    }


def _embd_state_counts(db: Session) -> dict[str, int]:
    rows = db.execute(select(EmbdChunk.state, func.count()).group_by(EmbdChunk.state)).all()

    counts = {
        "pending": 0,
        "ready": 0,
        "failed": 0,
        "in_progress": 0,
    }
    for state, count in rows:
        if state in counts:
            counts[state] = int(count)

    counts["total"] = counts["pending"] + counts["ready"] + counts["failed"] + counts["in_progress"]
    return counts


def _next_faiss_id_seed(db: Session) -> int:
    existing_max = (
        db.execute(
            select(EmbdChunk.faiss_id)
            .where(EmbdChunk.faiss_id.is_not(None))
            .order_by(EmbdChunk.faiss_id.desc())
            .limit(1)
        ).scalar_one_or_none()
    )
    if existing_max is None:
        return 1
    return int(existing_max) + 1


def _request_embedding_vectors(settings: Settings, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    endpoint = f"{_ollama_openai_base(settings.embedding_api_base)}/embeddings"
    payload = {
        "model": settings.embedding_model,
        "input": texts,
    }

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        body = response.read().decode("utf-8")

    parsed = json.loads(body)
    data = parsed.get("data", [])
    if not isinstance(data, list) or len(data) != len(texts):
        raise APIError("INTERNAL_ERROR", 500, "embedding response size mismatch")

    vectors: list[list[float]] = []
    for item in data:
        embedding = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(embedding, list) or not embedding:
            raise APIError("INTERNAL_ERROR", 500, "embedding response missing vector")

        vector = [float(value) for value in embedding]
        vectors.append(vector)

    return vectors


def _process_pending_embd_chunks(db: Session, settings: Settings, *, limit: int | None) -> dict[str, int | str | None]:
    stmt = (
        select(EmbdChunk)
        .where(EmbdChunk.state == "pending")
        .order_by(EmbdChunk.updated_at.asc(), EmbdChunk.doc_id.asc(), EmbdChunk.chunk_index.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    pending_rows = db.execute(stmt).scalars().all()
    pending_before = len(pending_rows)
    if pending_before == 0:
        return {
            "pending_before": 0,
            "processed": 0,
            "failed": 0,
            "skipped": 0,
            "remaining_pending": 0,
            "message": "no pending chunks",
        }

    if settings.embedding_provider != "ollama":
        return {
            "pending_before": pending_before,
            "processed": 0,
            "failed": 0,
            "skipped": pending_before,
            "remaining_pending": pending_before,
            "message": "only ollama embedding provider is supported",
        }

    batch_size = max(1, settings.embedding_batch_size)
    next_faiss_id = _next_faiss_id_seed(db)
    processed = 0
    failed = 0
    skipped = 0

    for batch_start in range(0, len(pending_rows), batch_size):
        batch = pending_rows[batch_start : batch_start + batch_size]
        ready_to_embed: list[tuple[EmbdChunk, CurrentChunk]] = []
        texts: list[str] = []

        for embd_chunk in batch:
            current_chunk = db.get(CurrentChunk, (embd_chunk.doc_id, embd_chunk.chunk_id))
            if current_chunk is None:
                if embd_chunk.faiss_id is not None:
                    _ = _faiss_remove_ids(settings, [embd_chunk.faiss_id])
                db.delete(embd_chunk)
                skipped += 1
                continue

            if embd_chunk.chunk_hash != current_chunk.chunk_hash:
                embd_chunk.chunk_hash = current_chunk.chunk_hash
                embd_chunk.chunk_index = current_chunk.chunk_index
                embd_chunk.state = "pending"
                embd_chunk.embedded_at = None
                embd_chunk.last_error = None
                embd_chunk.updated_at = utc_now()
                skipped += 1
                continue

            embd_chunk.state = "in_progress"
            embd_chunk.updated_at = utc_now()
            ready_to_embed.append((embd_chunk, current_chunk))
            texts.append(current_chunk.chunk_text)

        db.flush()

        if not ready_to_embed:
            continue

        try:
            vectors = _request_embedding_vectors(settings, texts)
            dim = len(vectors[0]) if vectors else 0
        except (APIError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            error_message = str(exc)
            for embd_chunk, _current_chunk in ready_to_embed:
                embd_chunk.state = "failed"
                embd_chunk.last_error = error_message[:2000]
                embd_chunk.updated_at = utc_now()
            failed += len(ready_to_embed)
            db.flush()
            continue

        faiss_records: list[tuple[EmbdChunk, list[float]]] = []

        for idx, (embd_chunk, current_chunk) in enumerate(ready_to_embed):
            latest_current = db.get(CurrentChunk, (embd_chunk.doc_id, embd_chunk.chunk_id))
            if latest_current is None:
                if embd_chunk.faiss_id is not None:
                    _ = _faiss_remove_ids(settings, [embd_chunk.faiss_id])
                db.delete(embd_chunk)
                skipped += 1
                continue

            if latest_current.chunk_hash != embd_chunk.chunk_hash:
                embd_chunk.chunk_hash = latest_current.chunk_hash
                embd_chunk.chunk_index = latest_current.chunk_index
                embd_chunk.state = "pending"
                embd_chunk.embedded_at = None
                embd_chunk.last_error = None
                embd_chunk.updated_at = utc_now()
                skipped += 1
                continue

            embd_chunk.chunk_index = current_chunk.chunk_index
            embd_chunk.embedding_model = settings.embedding_model
            embd_chunk.dim = dim
            if embd_chunk.faiss_id is None:
                embd_chunk.faiss_id = next_faiss_id
                next_faiss_id += 1

            faiss_records.append((embd_chunk, vectors[idx]))

        if faiss_records:
            try:
                _faiss_upsert_vectors(
                    settings,
                    [
                        (int(embd_chunk.faiss_id), vector)
                        for embd_chunk, vector in faiss_records
                        if embd_chunk.faiss_id is not None
                    ],
                    dimension=dim,
                )
            except APIError as exc:
                error_message = exc.message
                for embd_chunk, _vector in faiss_records:
                    embd_chunk.state = "failed"
                    embd_chunk.last_error = error_message[:2000]
                    embd_chunk.updated_at = utc_now()
                failed += len(faiss_records)
                db.flush()
                continue

        for embd_chunk, _vector in faiss_records:
            embd_chunk.state = "ready"
            embd_chunk.embedded_at = utc_now()
            embd_chunk.last_error = None
            embd_chunk.updated_at = utc_now()
            processed += 1

        db.flush()

    remaining_pending = int(
        db.execute(select(func.count()).select_from(EmbdChunk).where(EmbdChunk.state == "pending")).scalar_one()
    )
    return {
        "pending_before": pending_before,
        "processed": processed,
        "failed": failed,
        "skipped": skipped,
        "remaining_pending": remaining_pending,
        "message": None,
    }


def _faiss_search(settings: Settings, query_vector: list[float], *, top_k: int) -> list[tuple[int, float]]:
    if top_k <= 0:
        return []

    _lib, np_mod = _require_faiss_runtime()
    query_array = np_mod.asarray([query_vector], dtype=np_mod.float32)
    if query_array.ndim != 2 or int(query_array.shape[1]) <= 0:
        raise APIError("INVALID_INPUT", 400, "query vector is empty")

    dimension = int(query_array.shape[1])

    with _faiss_io_lock:
        index = _load_faiss_index_unlocked(settings, dimension=dimension)
        if index is None:
            return []

        total = int(index.ntotal)
        if total <= 0:
            return []

        candidate_k = min(total, max(top_k, top_k * 8, 32))
        try:
            distances, indices = index.search(query_array, candidate_k)
        except Exception as exc:
            raise APIError(
                "INDEXING_UNAVAILABLE",
                503,
                "failed to search faiss index",
                {"reason": str(exc)[:2000]},
            ) from exc

    pairs: list[tuple[int, float]] = []
    for raw_id, raw_score in zip(indices[0].tolist(), distances[0].tolist()):
        faiss_id = int(raw_id)
        if faiss_id < 0:
            continue
        pairs.append((faiss_id, float(raw_score)))
    return pairs


def _search_chunks_keyword(
    db: Session,
    settings: Settings,
    *,
    tokens: list[str],
    top_k: int,
    chunk_size: int,
    chunk_overlap: int,
    path_prefix: str | None,
) -> list[dict[str, Any]]:
    stmt = select(Document).where(Document.deleted_at.is_(None))

    if path_prefix is not None and path_prefix != "":
        normalized_prefix = normalize_rel_path(path_prefix)
        like_prefix = f"{normalized_prefix}/%"
        stmt = stmt.where(or_(Document.path == normalized_prefix, Document.path.like(like_prefix)))

    docs = db.execute(stmt.order_by(Document.path.asc())).scalars().all()

    scored: list[tuple[float, str, dict[str, Any]]] = []
    for document in docs:
        chunk_rows = db.execute(
            select(CurrentChunk)
            .where(CurrentChunk.doc_id == document.id)
            .order_by(CurrentChunk.chunk_index.asc())
        ).scalars().all()

        if chunk_rows:
            for chunk in chunk_rows:
                chunk_text = chunk.chunk_text
                score = keyword_score(chunk_text, tokens)
                if score <= 0:
                    continue

                start_offset = _chunk_start_offset(chunk.chunk_index)
                end_offset = start_offset + len(chunk_text)
                scored.append(
                    (
                        score,
                        document.path,
                        {
                            "doc_id": document.id,
                            "doc_path": document.path,
                            "chunk_id": chunk.chunk_id,
                            "chunk_index": chunk.chunk_index,
                            "chunk_start": start_offset,
                            "chunk_end": end_offset,
                            "snippet": make_snippet(chunk_text, tokens),
                        },
                    )
                )
            continue

        file_path = resolve_vault_path(settings.vault_root, document.path)
        if not file_path.exists() or not file_path.is_file():
            continue

        content = file_path.read_text(encoding="utf-8")
        chunks = split_text_chunks(content, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

        for chunk_index, start_offset, end_offset, chunk_text in chunks:
            score = keyword_score(chunk_text, tokens)
            if score <= 0:
                continue

            scored.append(
                (
                    score,
                    document.path,
                    {
                        "doc_id": document.id,
                        "doc_path": document.path,
                        "chunk_id": _chunk_id_from_index(chunk_index),
                        "chunk_index": chunk_index,
                        "chunk_start": start_offset,
                        "chunk_end": end_offset,
                        "snippet": make_snippet(chunk_text, tokens),
                    },
                )
            )

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[:top_k]]


def _search_chunks_vector(
    db: Session,
    settings: Settings,
    *,
    query: str,
    tokens: list[str],
    top_k: int,
    path_prefix: str | None,
) -> list[dict[str, Any]]:
    if settings.embedding_provider != "ollama":
        raise APIError("INDEXING_UNAVAILABLE", 503, "vector search requires ollama embedding provider")

    vectors = _request_embedding_vectors(settings, [query])
    query_vector = vectors[0] if vectors else []
    if not query_vector:
        return []

    faiss_pairs = _faiss_search(settings, query_vector, top_k=top_k)
    if not faiss_pairs:
        return []

    faiss_ids = [faiss_id for faiss_id, _score in faiss_pairs]
    if not faiss_ids:
        return []

    stmt = (
        select(EmbdChunk, CurrentChunk, Document)
        .join(
            CurrentChunk,
            (CurrentChunk.doc_id == EmbdChunk.doc_id) & (CurrentChunk.chunk_id == EmbdChunk.chunk_id),
        )
        .join(Document, Document.id == EmbdChunk.doc_id)
        .where(
            EmbdChunk.faiss_id.in_(faiss_ids),
            EmbdChunk.state == "ready",
            EmbdChunk.dim == len(query_vector),
            Document.deleted_at.is_(None),
        )
    )

    if settings.embedding_model:
        stmt = stmt.where(EmbdChunk.embedding_model == settings.embedding_model)

    if path_prefix is not None and path_prefix != "":
        normalized_prefix = normalize_rel_path(path_prefix)
        like_prefix = f"{normalized_prefix}/%"
        stmt = stmt.where(or_(Document.path == normalized_prefix, Document.path.like(like_prefix)))

    rows = db.execute(stmt).all()
    mapped: dict[int, tuple[CurrentChunk, Document]] = {}
    for embd_chunk, current_chunk, document in rows:
        if embd_chunk.faiss_id is None:
            continue
        if embd_chunk.chunk_hash != current_chunk.chunk_hash:
            continue
        mapped[int(embd_chunk.faiss_id)] = (current_chunk, document)

    hits: list[dict[str, Any]] = []
    for faiss_id, _score in faiss_pairs:
        mapped_row = mapped.get(faiss_id)
        if mapped_row is None:
            continue

        current_chunk, document = mapped_row
        chunk_text = current_chunk.chunk_text
        start_offset = _chunk_start_offset(current_chunk.chunk_index)
        end_offset = start_offset + len(chunk_text)
        hits.append(
            {
                "doc_id": document.id,
                "doc_path": document.path,
                "chunk_id": current_chunk.chunk_id,
                "chunk_index": current_chunk.chunk_index,
                "chunk_start": start_offset,
                "chunk_end": end_offset,
                "snippet": make_snippet(chunk_text, tokens),
            }
        )

        if len(hits) >= top_k:
            break

    return hits


def _int_stat(value: int | str | None) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def run_embedding_sync(db: Session, settings: Settings, *, limit: int | None = None, reason: str) -> dict[str, Any]:
    del reason

    acquired = _embedding_sync_lock.acquire(blocking=False)
    if not acquired:
        status = get_embedding_sync_status(db, settings)
        return {
            "started": False,
            "running": bool(status["running"]),
            "provider": settings.embedding_provider,
            "refreshed_docs": 0,
            "created_embd": 0,
            "updated_embd": 0,
            "deleted_embd": 0,
            "pending_before": 0,
            "processed": 0,
            "failed": 0,
            "skipped": 0,
            "remaining_pending": int(status["pending"]),
            "message": "embedding sync is already running",
        }

    global _embedding_last_run_at
    try:
        _ = bootstrap_documents_from_vault(db, settings)
        refresh_stats = _refresh_current_chunks_from_documents(db, settings)
        reconcile_stats = _reconcile_embd_table(db, settings)
        process_stats = _process_pending_embd_chunks(db, settings, limit=limit)
        _embedding_last_run_at = utc_now()

        return {
            "started": True,
            "running": False,
            "provider": settings.embedding_provider,
            "refreshed_docs": int(refresh_stats["refreshed_docs"]),
            "created_embd": int(reconcile_stats["created"]),
            "updated_embd": int(reconcile_stats["updated"]),
            "deleted_embd": int(reconcile_stats["deleted"]),
            "pending_before": _int_stat(process_stats["pending_before"]),
            "processed": _int_stat(process_stats["processed"]),
            "failed": _int_stat(process_stats["failed"]),
            "skipped": _int_stat(process_stats["skipped"]),
            "remaining_pending": _int_stat(process_stats["remaining_pending"]),
            "message": process_stats["message"],
        }
    finally:
        _embedding_sync_lock.release()


def get_embedding_sync_status(db: Session, settings: Settings) -> dict[str, Any]:
    counts = _embd_state_counts(db)
    return {
        "running": _embedding_sync_lock.locked(),
        "provider": settings.embedding_provider,
        "pending": counts["pending"],
        "ready": counts["ready"],
        "failed": counts["failed"],
        "in_progress": counts["in_progress"],
        "total": counts["total"],
        "last_run_at": _embedding_last_run_at,
    }


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
    stmt = select(Document).where(Document.deleted_at.is_(None))

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


def _load_sync_payload(change: SyncChange) -> dict[str, Any]:
    try:
        parsed = json.loads(change.payload) if change.payload else {}
    except json.JSONDecodeError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


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


def search_chunks(
    db: Session,
    settings: Settings,
    *,
    query: str,
    mode: str,
    top_k: int,
    chunk_size: int,
    chunk_overlap: int,
    path_prefix: str | None,
) -> dict[str, Any]:
    if mode not in {"keyword", "vector"}:
        raise APIError("INVALID_INPUT", 400, "mode must be keyword or vector")

    tokens = tokenize(query)
    if not tokens:
        raise APIError("INVALID_INPUT", 400, "q must contain at least one token")

    if chunk_size <= 0:
        raise APIError("INVALID_INPUT", 400, "chunk_size must be positive")
    if chunk_overlap < 0:
        raise APIError("INVALID_INPUT", 400, "chunk_overlap must be non-negative")
    if chunk_overlap >= chunk_size:
        raise APIError("INVALID_INPUT", 400, "chunk_overlap must be smaller than chunk_size")

    effective_mode = mode
    if mode == "vector" and settings.embedding_provider != "ollama":
        raise APIError("INDEXING_UNAVAILABLE", 503, "vector search requires ollama embedding provider")

    if effective_mode == "keyword":
        hits = _search_chunks_keyword(
            db,
            settings,
            tokens=tokens,
            top_k=top_k,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            path_prefix=path_prefix,
        )
        return {"hits": hits}

    if effective_mode == "vector":
        hits = _search_chunks_vector(
            db,
            settings,
            query=query,
            tokens=tokens,
            top_k=top_k,
            path_prefix=path_prefix,
        )
        return {"hits": hits}

    raise APIError("INDEXING_UNAVAILABLE", 503, "unsupported search mode")


AUTH_COOKIE_NAME = "slo_auth_token"
AUTH_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30

MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024

_ALLOWED_IMAGE_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

_ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

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


def _choose_image_extension(filename: str, content_type: str) -> str:
    file_ext = Path(filename).suffix.lower()
    if file_ext in _ALLOWED_IMAGE_EXTS:
        return ".jpg" if file_ext == ".jpeg" else file_ext

    mapped = _ALLOWED_IMAGE_MIME_TO_EXT.get(content_type)
    if mapped:
        return mapped

    raise APIError("INVALID_INPUT", 400, "unsupported image file extension")


def _validate_image_bytes(content_type: str, data: bytes) -> None:
    if content_type not in _ALLOWED_IMAGE_MIME_TO_EXT:
        raise APIError("INVALID_INPUT", 400, "unsupported image content type")

    size = len(data)
    if size <= 0:
        raise APIError("INVALID_INPUT", 400, "image file is empty")
    if size > MAX_IMAGE_UPLOAD_BYTES:
        raise APIError("INVALID_INPUT", 400, "image file is too large")

    signatures: tuple[tuple[bytes, str], ...] = (
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
    )
    matched: str | None = None
    for signature, mime in signatures:
        if data.startswith(signature):
            matched = mime
            break

    if matched is None and len(data) >= 12:
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            matched = "image/webp"

    if matched is None or matched != content_type:
        raise APIError("INVALID_INPUT", 400, "invalid image file signature")


def _normalize_image_rel_path(path: str) -> str:
    rel_path = normalize_rel_path(path)
    if not rel_path.startswith("images/"):
        raise APIError("PATH_OUT_OF_VAULT", 400, "path must be under images/")

    suffix = Path(rel_path).suffix.lower()
    if suffix not in _ALLOWED_IMAGE_EXTS:
        raise APIError("INVALID_INPUT", 400, "unsupported image extension", {"path": rel_path})

    return rel_path


def upload_image_to_vault(
    settings: Settings,
    *,
    filename: str,
    content_type: str,
    data: bytes,
) -> dict[str, Any]:
    safe_filename = Path(str(filename or "")).name
    if safe_filename.strip() == "":
        raise APIError("INVALID_INPUT", 400, "filename is required")

    _validate_image_bytes(content_type, data)
    extension = _choose_image_extension(safe_filename, content_type)

    base_name = Path(safe_filename).stem
    candidate_path = f"images/{base_name}{extension}"
    image_abs = resolve_vault_path(settings.vault_root, normalize_rel_path(candidate_path))

    if image_abs.exists():
        candidate_path = f"images/{base_name}_{uuid4().hex[:8]}{extension}"
        image_abs = resolve_vault_path(settings.vault_root, normalize_rel_path(candidate_path))
    
    rel_path = normalize_rel_path(candidate_path)
    atomic_write_bytes(image_abs, data)

    encoded = quote(rel_path, safe="")
    url = f"{settings.api_prefix}/images/by-path?path={encoded}"
    markdown = f"![{Path(safe_filename).stem}]({url})"

    return {
        "path": rel_path,
        "url": url,
        "markdown": markdown,
        "content_type": content_type,
        "size": len(data),
    }


def get_image_path(settings: Settings, path: str) -> dict[str, Any]:
    rel_path = _normalize_image_rel_path(path)

    image_abs = resolve_vault_path(settings.vault_root, rel_path)
    if not image_abs.exists() or not image_abs.is_file():
        raise APIError("NOT_FOUND", 404, "image not found", {"path": rel_path})

    suffix = image_abs.suffix.lower()
    media_type_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_type_map.get(suffix)
    if media_type is None:
        raise APIError("INVALID_INPUT", 400, "unsupported image extension", {"path": rel_path})

    return {"abs_path": image_abs, "media_type": media_type, "path": rel_path}


def delete_image_from_vault(settings: Settings, path: str) -> dict[str, Any]:
    rel_path = _normalize_image_rel_path(path)
    image_abs = resolve_vault_path(settings.vault_root, rel_path)

    if not image_abs.exists() or not image_abs.is_file():
        raise APIError("NOT_FOUND", 404, "image not found", {"path": rel_path})

    image_abs.unlink()
    return {"path": rel_path, "deleted": True}


def rename_image_in_vault(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    from_path = _normalize_image_rel_path(str(payload.get("from_path", "")))

    raw_to_path = str(payload.get("to_path", "")).strip()
    if raw_to_path == "":
        raise APIError("INVALID_INPUT", 400, "to_path is required")

    if "/" not in raw_to_path and "\\" not in raw_to_path:
        parent = PurePosixPath(from_path).parent.as_posix()
        raw_to_path = f"{parent}/{raw_to_path}" if parent and parent != "." else raw_to_path

    to_path = _normalize_image_rel_path(raw_to_path)

    from_ext = Path(from_path).suffix.lower()
    to_ext = Path(to_path).suffix.lower()
    if from_ext != to_ext:
        raise APIError("INVALID_INPUT", 400, "image extension cannot be changed")

    if from_path == to_path:
        return {"from_path": from_path, "to_path": to_path}

    source_abs = resolve_vault_path(settings.vault_root, from_path)
    if not source_abs.exists() or not source_abs.is_file():
        raise APIError("NOT_FOUND", 404, "source image not found", {"path": from_path})

    target_abs = resolve_vault_path(settings.vault_root, to_path)
    if target_abs.exists():
        if target_abs.is_dir():
            raise APIError("CONFLICT", 409, "target path already exists as folder", {"path": to_path})
        if not bool(payload.get("overwrite", False)):
            raise APIError("CONFLICT", 409, "target image already exists", {"path": to_path})
        target_abs.unlink()

    move_path(source_abs, target_abs)
    return {"from_path": from_path, "to_path": to_path}


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


def _normalize_chat_session_id(raw_value: Any) -> str | None:
    if raw_value is None:
        return None
    session_id = str(raw_value).strip()
    if session_id == "":
        return None
    return session_id


def _serialize_chat_session(session: ChatSession) -> dict[str, Any]:
    title = session.title.strip() if session.title and session.title.strip() else "Chat"
    return {
        "session_id": session.session_id,
        "title": title,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def _serialize_chat_message(message: ChatMessage) -> dict[str, Any]:
    role = message.role if message.role in {"user", "assistant"} else "assistant"

    raw_paths: Any
    try:
        raw_paths = json.loads(message.source_doc_paths)
    except json.JSONDecodeError:
        raw_paths = []

    source_doc_paths: list[str] = []
    if isinstance(raw_paths, list):
        for item in raw_paths:
            path = str(item).strip()
            if path and path not in source_doc_paths:
                source_doc_paths.append(path)

    return {
        "message_id": message.message_id,
        "session_id": message.session_id,
        "role": role,
        "content": message.content,
        "source_doc_paths": source_doc_paths,
        "created_at": message.created_at,
    }


def _create_chat_session_entity(db: Session, title: str | None = None) -> ChatSession:
    session_title = title.strip() if isinstance(title, str) and title.strip() else "Chat"
    now = utc_now()
    session = ChatSession(
        session_id=str(uuid4()),
        title=session_title,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    return session


def _get_chat_session_or_none(db: Session, session_id: str) -> ChatSession | None:
    return db.get(ChatSession, session_id)


def _require_chat_session(db: Session, session_id: str) -> ChatSession:
    session = _get_chat_session_or_none(db, session_id)
    if session is None:
        raise APIError("NOT_FOUND", 404, "chat session not found", {"session_id": session_id})
    return session


def _get_chat_message_or_none(db: Session, message_id: str) -> ChatMessage | None:
    return db.get(ChatMessage, message_id)


def _require_chat_message(db: Session, message_id: str) -> ChatMessage:
    message = _get_chat_message_or_none(db, message_id)
    if message is None:
        raise APIError("NOT_FOUND", 404, "chat message not found", {"message_id": message_id})
    return message


def _append_chat_message(
    db: Session,
    *,
    session_id: str,
    role: str,
    content: str,
    source_doc_paths: list[str] | None = None,
) -> ChatMessage:
    normalized_role = role.strip().lower()
    if normalized_role not in {"user", "assistant"}:
        raise APIError("INVALID_INPUT", 400, "invalid chat role", {"role": role})

    normalized_paths: list[str] = []
    if isinstance(source_doc_paths, list):
        for item in source_doc_paths:
            path = str(item).strip()
            if path == "" or path in normalized_paths:
                continue
            normalized_paths.append(path)

    message = ChatMessage(
        message_id=str(uuid4()),
        session_id=session_id,
        role=normalized_role,
        content=content,
        source_doc_paths=json.dumps(normalized_paths, ensure_ascii=True),
        created_at=utc_now(),
    )
    db.add(message)
    return message


def _load_session_history(db: Session, session_id: str, *, max_messages: int = 12) -> list[dict[str, str]]:
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    rows = db.execute(stmt).scalars().all()
    normalized: list[dict[str, str]] = []
    for row in rows[-max_messages:]:
        if row.role not in {"user", "assistant"}:
            continue
        content = row.content.strip()
        if content == "":
            continue
        normalized.append({"role": row.role, "content": content[:4000]})
    return normalized


def _list_session_messages(db: Session, session_id: str) -> Sequence[ChatMessage]:
    rows = db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    ).scalars().all()
    return list(rows)


def _history_from_message_rows(rows: Sequence[ChatMessage], *, max_messages: int = 12) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in rows[-max_messages:]:
        if row.role not in {"user", "assistant"}:
            continue
        content = row.content.strip()
        if content == "":
            continue
        normalized.append({"role": row.role, "content": content[:4000]})
    return normalized


def _paired_assistant_after(messages: list[ChatMessage], user_index: int) -> ChatMessage | None:
    next_index = user_index + 1
    if next_index >= len(messages):
        return None
    candidate = messages[next_index]
    if candidate.role == "assistant":
        return candidate
    return None


def _source_doc_paths_from_hits(hits: list[dict[str, Any]]) -> list[str]:
    source_doc_paths: list[str] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        path = str(hit.get("doc_path", "")).strip()
        if path == "" or path in source_doc_paths:
            continue
        source_doc_paths.append(path)
    return source_doc_paths


def _generate_chat_session_title(
    *,
    api_base: str,
    model: str,
    query: str,
    answer: str,
) -> str | None:
    endpoint = f"{_ollama_openai_base(api_base)}/chat/completions"
    system_prompt = dedent(
        """
        Generate a concise chat title based on the first user question and assistant answer.
        Return plain text only.
        Do not use quotes, markdown, bullets, or prefixes.
        Keep it under 48 characters.
        """
    ).strip()
    user_prompt = dedent(
        f"""
        User question:
        {query}

        Assistant answer:
        {answer}
        """
    ).strip()

    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        body = response.read().decode("utf-8")

    parsed = json.loads(body)
    choices = parsed.get("choices", [])
    if not choices:
        return None

    message = choices[0].get("message", {})
    content = str(message.get("content", "")).strip()
    if content == "":
        return None

    title = content.splitlines()[0].strip().strip('"').strip("'")
    title = title[:48].strip()
    if title == "":
        return None
    return title


def list_chat_sessions(db: Session) -> dict[str, Any]:
    sessions = db.execute(
        select(ChatSession).order_by(ChatSession.updated_at.desc(), ChatSession.created_at.desc())
    ).scalars().all()
    return {"sessions": [_serialize_chat_session(session) for session in sessions]}


def create_chat_session(db: Session, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    title_value: str | None = None
    if isinstance(payload, dict):
        raw = payload.get("title")
        if isinstance(raw, str):
            title_value = raw

    session = _create_chat_session_entity(db, title=title_value)
    return _serialize_chat_session(session)


def update_chat_session(db: Session, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    session = _require_chat_session(db, session_id)
    title = str(payload.get("title", "")).strip()
    if title == "":
        raise APIError("INVALID_INPUT", 400, "title is required")

    session.title = title[:256]
    session.updated_at = utc_now()
    db.flush()
    return _serialize_chat_session(session)


def delete_chat_session(db: Session, session_id: str) -> dict[str, Any]:
    session = _require_chat_session(db, session_id)

    messages = db.execute(select(ChatMessage).where(ChatMessage.session_id == session_id)).scalars().all()
    for message in messages:
        db.delete(message)

    db.delete(session)
    db.flush()
    return {"deleted": True}


def get_chat_session_messages(db: Session, session_id: str) -> dict[str, Any]:
    session = _require_chat_session(db, session_id)
    messages = db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    ).scalars().all()
    return {
        "session": _serialize_chat_session(session),
        "messages": [_serialize_chat_message(message) for message in messages],
    }


def update_chat_message(db: Session, settings: Settings, message_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    message = _require_chat_message(db, message_id)
    if message.role != "user":
        raise APIError("INVALID_INPUT", 400, "only user messages can be edited")

    content = str(payload.get("content", "")).strip()
    if content == "":
        raise APIError("INVALID_INPUT", 400, "content is required")

    parent = _require_chat_session(db, message.session_id)
    session_messages = list(_list_session_messages(db, parent.session_id))
    target_index = next((idx for idx, row in enumerate(session_messages) if row.message_id == message.message_id), -1)
    if target_index < 0:
        raise APIError("NOT_FOUND", 404, "chat message not found", {"message_id": message_id})

    paired_assistant = _paired_assistant_after(session_messages, target_index)
    history = _history_from_message_rows(session_messages[:target_index], max_messages=12)
    api_base = settings.llm_api_base
    model = settings.llm_model

    rag_result = _run_rag_answer_result(
        db,
        settings,
        query=content,
        history=history,
        top_k=6,
        chunk_size=800,
        chunk_overlap=120,
        temperature=0.2,
        api_base=api_base,
        model=model,
    )

    message.content = content
    message.source_doc_paths = json.dumps([], ensure_ascii=True)

    if paired_assistant is None:
        _append_chat_message(
            db,
            session_id=parent.session_id,
            role="assistant",
            content=str(rag_result["answer"]),
            source_doc_paths=list(rag_result["source_doc_paths"]),
        )
    else:
        paired_assistant.content = str(rag_result["answer"])
        paired_assistant.source_doc_paths = json.dumps(list(rag_result["source_doc_paths"]), ensure_ascii=True)

    parent.updated_at = utc_now()
    db.flush()
    return _serialize_chat_message(message)


def delete_chat_message(db: Session, message_id: str) -> dict[str, Any]:
    message = _require_chat_message(db, message_id)
    if message.role != "user":
        raise APIError("INVALID_INPUT", 400, "only user messages can be deleted")

    parent = _require_chat_session(db, message.session_id)
    session_messages = list(_list_session_messages(db, parent.session_id))
    target_index = next((idx for idx, row in enumerate(session_messages) if row.message_id == message.message_id), -1)
    if target_index < 0:
        raise APIError("NOT_FOUND", 404, "chat message not found", {"message_id": message_id})

    paired_assistant = _paired_assistant_after(session_messages, target_index)

    db.delete(message)
    if paired_assistant is not None:
        db.delete(paired_assistant)

    parent.updated_at = utc_now()
    db.flush()
    return {"deleted": True}


def _fallback_rag_answer(query: str, hits: list[dict[str, Any]]) -> str:
    del query

    if not hits:
        return "I cannot find the answer in the provided context."

    lines: list[str] = []
    for hit in hits[:5]:
        doc_path = str(hit.get("doc_path", ""))
        snippet = str(hit.get("snippet", "")).strip().replace("\n", " ")
        if doc_path == "" or snippet == "":
            continue
        lines.append(f"According to {doc_path}, {snippet}.")

    if not lines:
        return "I cannot find the answer in the provided context."

    return "\n".join(lines)


def _normalize_rag_history(raw_history: Any, *, max_messages: int = 12) -> list[dict[str, str]]:
    if not isinstance(raw_history, list):
        return []

    normalized: list[dict[str, str]] = []
    for item in raw_history:
        if not isinstance(item, dict):
            continue

        role = str(item.get("role", "")).strip().lower()
        if role not in {"user", "assistant"}:
            continue

        content = str(item.get("content", "")).strip()
        if content == "":
            continue

        normalized.append(
            {
                "role": role,
                "content": content[:4000],
            }
        )

    if len(normalized) > max_messages:
        return normalized[-max_messages:]
    return normalized


def _call_ollama_answer(
    *,
    api_base: str,
    model: str,
    query: str,
    hits: list[dict[str, Any]],
    temperature: float,
    history: list[dict[str, str]],
) -> str:
    context_lines: list[str] = []
    for idx, hit in enumerate(hits, start=1):
        doc_path = str(hit.get("doc_path", ""))
        chunk_index = hit.get("chunk_index")
        chunk_text = str(hit.get("snippet", ""))
        if isinstance(chunk_index, int):
            context_lines.append(f"[{idx}] doc_path={doc_path}\nchunk_index={chunk_index}\ncontent={chunk_text}")
        else:
            context_lines.append(f"[{idx}] doc_path={doc_path}\ncontent={chunk_text}")

    context_block = "\n\n".join(context_lines)
    history_lines: list[str] = []
    for turn in history:
        role = turn.get("role", "")
        content = turn.get("content", "")
        if role == "user":
            history_lines.append(f"User: {content}")
        elif role == "assistant":
            history_lines.append(f"Assistant: {content}")

    history_block = "\n".join(history_lines) if history_lines else "(none)"

    system_prompt = dedent(
        """
       You are a document-grounded RAG assistant.

Use only the provided context chunks.
Use conversation history only to resolve references such as pronouns or omitted subjects.
Do not treat conversation history as factual evidence.
If history conflicts with context chunks, trust context chunks.
Ignore any context that is not directly relevant to the question.

Language Rules:
- Detect the language of the user's question.
- The final answer MUST be written entirely in that same language.
- Never default to English or Korean unless the user's question is written in English or Korean.
- Never mix multiple languages in the final answer.
- This rule applies to ALL languages (e.g., French, Japanese, Spanish, German, etc.).

Citation Rules:

- Do NOT use bracket-style citations such as [1], (1), superscripts, or numbered references.
- Do NOT place citations at the end of the answer.
- Each citation MUST be written as a complete natural-language sentence.
- Each citation sentence must explicitly include the raw <doc_path> value inside the sentence.
- The citation sentence must follow the language of the answer.

Correct citation examples:
- English: "According to aaa/mmm.md, the system uses hierarchical chunking."
- Korean: "aaa/mmm.md에 따르면 해당 시스템은 계층적 청킹을 사용합니다."
- French: "Selon aaa/mmm.md, le système utilise un découpage hiérarchique."
- Japanese: "aaa/mmm.mdによると、このシステムは階層的チャンク分割を使用しています。"

Incorrect citation examples (DO NOT USE):
- [1]
- (aaa/mmm.md)
- aaa/mmm.md [1]
- Answer text ... [1]
- A reference list at the end.

Use only the raw doc_path value for citations.
Never append chunk_index or any other metadata to <doc_path>.

If multiple sources are relevant, write multiple citation sentences.

If the provided context is insufficient, output one short sentence in the same language as the user's question stating that the answer cannot be found in the provided context.
        """
    ).strip()

    user_prompt = dedent(
        f"""
        Conversation history:
        {history_block}

        Question:
        {query}

        Context chunks:
        {context_block}
        """
    ).strip()

    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    endpoint = f"{_ollama_openai_base(api_base)}/chat/completions"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read().decode("utf-8")

    parsed = json.loads(body)
    choices = parsed.get("choices", [])
    if not choices:
        raise APIError("INTERNAL_ERROR", 500, "llm response did not contain choices")

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if not isinstance(content, str) or content.strip() == "":
        raise APIError("INTERNAL_ERROR", 500, "llm response content is empty")

    return content.strip()


def _run_rag_answer_result(
    db: Session,
    settings: Settings,
    *,
    query: str,
    history: list[dict[str, str]],
    top_k: int,
    chunk_size: int,
    chunk_overlap: int,
    temperature: float,
    api_base: str,
    model: str,
) -> dict[str, Any]:
    result = search_chunks(
        db,
        settings,
        query=query,
        mode="vector",
        top_k=top_k,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        path_prefix=None,
    )

    hits = result.get("hits", [])
    if not isinstance(hits, list):
        hits = []

    source_doc_paths = _source_doc_paths_from_hits(hits)

    degraded = True
    answer = _fallback_rag_answer(query, hits)
    used_model = "fallback-rag"

    try:
        answer = _call_ollama_answer(
            api_base=api_base,
            model=model,
            query=query,
            hits=hits,
            temperature=temperature,
            history=history,
        )
        used_model = model
        degraded = False
    except (APIError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        degraded = True

    return {
        "answer": answer,
        "hits": hits,
        "source_doc_paths": source_doc_paths,
        "model": used_model,
        "degraded": degraded,
    }


def answer_with_rag(db: Session, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query", "")).strip()
    if query == "":
        raise APIError("INVALID_INPUT", 400, "query is required")

    requested_session_id = _normalize_chat_session_id(payload.get("session_id"))
    top_k = int(payload.get("top_k", 6))
    chunk_size = int(payload.get("chunk_size", 800))
    chunk_overlap = int(payload.get("chunk_overlap", 120))
    temperature = float(payload.get("temperature", 0.2))

    if requested_session_id is None:
        session = _create_chat_session_entity(db, title="Chat")
    else:
        session = _require_chat_session(db, requested_session_id)

    history = _load_session_history(db, session.session_id, max_messages=12)
    if not history:
        history = _normalize_rag_history(payload.get("history"))

    api_base = (payload.get("api_base") or settings.llm_api_base).strip()
    model = (payload.get("model") or settings.llm_model).strip()

    rag_result = _run_rag_answer_result(
        db,
        settings,
        query=query,
        history=history,
        top_k=top_k,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        temperature=temperature,
        api_base=api_base,
        model=model,
    )

    answer = str(rag_result["answer"])
    hits = list(rag_result["hits"])
    source_doc_paths = list(rag_result["source_doc_paths"])
    used_model = str(rag_result["model"])
    degraded = bool(rag_result["degraded"])

    was_initial_turn = len(history) == 0
    _append_chat_message(db, session_id=session.session_id, role="user", content=query, source_doc_paths=[])
    _append_chat_message(
        db,
        session_id=session.session_id,
        role="assistant",
        content=answer,
        source_doc_paths=source_doc_paths,
    )
    session.updated_at = utc_now()

    current_title = session.title.strip() if session.title else ""
    if was_initial_turn and (current_title == "" or current_title.lower() == "chat"):
        try:
            generated_title = _generate_chat_session_title(
                api_base=api_base,
                model=model,
                query=query,
                answer=answer,
            )
        except (APIError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            generated_title = None

        if generated_title:
            session.title = generated_title
            session.updated_at = utc_now()

    session_title = session.title.strip() if session.title and session.title.strip() else "Chat"
    db.flush()

    return {
        "session_id": session.session_id,
        "session_title": session_title,
        "answer": answer,
        "sources": hits,
        "model": used_model,
        "degraded": degraded,
    }


def _parent_rel_path(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "" if parent == "." else parent


def _collect_folder_paths(doc_paths: list[str]) -> list[str]:
    folders: set[str] = set()
    for path in doc_paths:
        current = _parent_rel_path(path)
        while current:
            folders.add(current)
            current = _parent_rel_path(current)
    return sorted(folders)


def _folder_updated_at(folder_path: str, doc_nodes: list[dict[str, Any]]) -> datetime:
    descendants = [node for node in doc_nodes if node["path"].startswith(f"{folder_path}/")]
    if descendants:
        return max(node["updated_at"] for node in descendants)
    return utc_now()


def _build_folder_tree_edges(folder_paths: list[str], doc_nodes: list[dict[str, Any]], top_k_edges: int) -> list[dict[str, Any]]:
    if top_k_edges <= 0:
        return []

    folder_set = set(folder_paths)
    edges: list[dict[str, Any]] = []

    for folder_path in folder_paths:
        parent = _parent_rel_path(folder_path)
        if parent and parent in folder_set:
            edges.append(
                {
                    "from_node_id": f"folder:{parent}",
                    "to_node_id": f"folder:{folder_path}",
                    "edge_type": "folder_tree",
                    "weight": 1.0,
                }
            )

    for doc_node in doc_nodes:
        parent = _parent_rel_path(doc_node["path"])
        if parent and parent in folder_set:
            edges.append(
                {
                    "from_node_id": f"folder:{parent}",
                    "to_node_id": doc_node["node_id"],
                    "edge_type": "folder_tree",
                    "weight": 1.0,
                }
            )

    return edges


def get_graph(
    db: Session,
    *,
    layout: str,
    include_edges: bool,
    top_k_edges: int,
    path_prefix: str | None,
) -> dict[str, Any]:
    if layout != "pca":
        raise APIError("INVALID_INPUT", 400, "only pca layout is supported", {"layout": layout})

    doc_stmt = select(Document).where(Document.deleted_at.is_(None))
    if path_prefix is not None and path_prefix != "":
        normalized_prefix = normalize_rel_path(path_prefix)
        like_prefix = f"{normalized_prefix}/%"
        doc_stmt = doc_stmt.where(or_(Document.path == normalized_prefix, Document.path.like(like_prefix)))

    docs = db.execute(doc_stmt.order_by(Document.path.asc())).scalars().all()
    doc_nodes: list[dict[str, Any]] = []
    for document in docs:
        doc_nodes.append(
            {
                "node_id": f"doc:{document.id}",
                "node_type": "doc",
                "doc_id": document.id,
                "path": document.path,
                "title": document.title,
                "x": None,
                "y": None,
                "z": None,
                "layout": layout,
                "updated_at": document.updated_at,
            }
        )

    folder_paths = _collect_folder_paths([node["path"] for node in doc_nodes])
    folder_nodes: list[dict[str, Any]] = []
    for folder_path in folder_paths:
        updated_at = _folder_updated_at(folder_path, doc_nodes)
        folder_nodes.append(
            {
                "node_id": f"folder:{folder_path}",
                "node_type": "folder",
                "doc_id": None,
                "path": folder_path,
                "title": PurePosixPath(folder_path).name,
                "x": None,
                "y": None,
                "z": None,
                "layout": layout,
                "updated_at": updated_at,
            }
        )

    nodes = [*folder_nodes, *doc_nodes]
    edges = _build_folder_tree_edges(folder_paths, doc_nodes, top_k_edges) if include_edges else []

    return {
        "data": {
            "layout": layout,
            "nodes": nodes,
            "edges": edges,
        },
        "meta": _request_meta(),
    }


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
