from __future__ import annotations

import json
import urllib.error
import urllib.request
from textwrap import dedent
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import APIError

from .service_shared import _ollama_openai_base
from .search import search_chunks


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

    with urllib.request.urlopen(request, timeout=30) as response:
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
