from __future__ import annotations

import json
import urllib.error
import urllib.request
from textwrap import dedent
from typing import Any, Sequence
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import APIError
from app.models import ChatMessage, ChatSession, utc_now

from .service_shared import _ollama_openai_base
from .rag import _normalize_rag_history, _run_rag_answer_result


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

    with urllib.request.urlopen(request, timeout=20) as response:
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
