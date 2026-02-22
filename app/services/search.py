from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import APIError
from app.indexing import keyword_score, make_snippet, split_text_chunks, tokenize
from app.models import CurrentChunk, Document, EmbdChunk
from app.vault import normalize_rel_path, resolve_vault_path

from .embeddings import (
    _faiss_io_lock,
    _load_faiss_index_unlocked,
    _request_embedding_vectors,
    _require_faiss_runtime,
)
from .service_shared import (
    _chunk_id_from_index,
    _chunk_start_offset,
)


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
