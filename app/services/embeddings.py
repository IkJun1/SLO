from __future__ import annotations

import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import APIError
from app.indexing import hash_text
from app.models import CurrentChunk, Document, EmbdChunk, utc_now
from app.vault import resolve_vault_path

from .service_shared import _ollama_openai_base, _set_doc_chunks, _title_from_content

try:
    import faiss
except ImportError:
    faiss = None

try:
    import numpy as np
except ImportError:
    np = None


_embedding_sync_lock = threading.Lock()
_faiss_io_lock = threading.Lock()
_embedding_last_run_at: datetime | None = None


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

    with urllib.request.urlopen(request, timeout=60) as response:
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
