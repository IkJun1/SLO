from __future__ import annotations

import hashlib
from collections.abc import Iterable


def hash_text(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def tokenize(query: str) -> list[str]:
    return [token for token in query.lower().split() if token]


def keyword_score(text: str, tokens: Iterable[str]) -> float:
    lowered = text.lower()
    score = 0.0
    matched = 0
    token_list = list(tokens)

    if not token_list:
        return 0.0

    for token in token_list:
        count = lowered.count(token)
        if count > 0:
            matched += 1
            score += 1.0 + max(0, count - 1) * 0.25

    if matched == 0:
        return 0.0

    return score / float(len(token_list))


def make_snippet(text: str, tokens: Iterable[str], max_len: int = 200) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_len:
        return compact

    lowered = compact.lower()
    best_index = 0
    for token in tokens:
        idx = lowered.find(token.lower())
        if idx >= 0:
            best_index = idx
            break

    start = max(0, best_index - max_len // 3)
    end = min(len(compact), start + max_len)
    snippet = compact[start:end].strip()

    if start > 0:
        snippet = f"...{snippet}"
    if end < len(compact):
        snippet = f"{snippet}..."

    return snippet


def split_text_chunks(content: str, *, chunk_size: int, chunk_overlap: int) -> list[tuple[int, int, int, str]]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap must be non-negative")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    normalized = content.replace("\r\n", "\n")
    if normalized == "":
        return []

    step = chunk_size - chunk_overlap
    chunks: list[tuple[int, int, int, str]] = []
    cursor = 0
    chunk_index = 0
    text_len = len(normalized)

    while cursor < text_len:
        end = min(text_len, cursor + chunk_size)
        chunks.append((chunk_index, cursor, end, normalized[cursor:end]))
        if end >= text_len:
            break
        cursor += step
        chunk_index += 1

    return chunks
