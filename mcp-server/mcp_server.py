from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from mcp.server.fastmcp import FastMCP


API_BASE = os.getenv("SLO_API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_PREFIX = os.getenv("SLO_MCP_API_PREFIX", "/api/v1/mcp").strip()
REQUEST_TIMEOUT_SECONDS = float(os.getenv("SLO_MCP_TIMEOUT_SECONDS", "30"))
MCP_INSTRUCTIONS = """
SLO MCP Tool Usage Policy
1. Documents must always be created with .md extension.
2. For document read, update, delete, move, and create requests, call tree first to confirm path structure before executing other tools.
3. If the user does not provide an exact target path for document creation, infer a reasonable path based on the request and create there.
4. When needed for document creation, it is allowed to create missing parent folders and then create the document at the requested path.
5. Do not modify paths outside the user-requested scope.
6. After any document read, update, delete, move, or create operation, explicitly report the exact path used.
7. Run delete_doc or move_doc with overwrite=true only when user intent is explicit.
""".strip()

if not API_PREFIX.startswith("/"):
    API_PREFIX = f"/{API_PREFIX}"
API_PREFIX = API_PREFIX.rstrip("/")

mcp = FastMCP("slo-mcp", instructions=MCP_INSTRUCTIONS)


def _get_mcp_api_key() -> str:
    key = os.getenv("MCP_API_KEY", "").strip()
    if key == "":
        raise RuntimeError("MCP_API_KEY is required")
    return key


def _build_url(path: str, params: dict[str, Any] | None = None) -> str:
    normalized_path = f"/{path.lstrip('/')}"
    url = f"{API_BASE}{API_PREFIX}{normalized_path}"
    if not params:
        return url

    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            serialized = "true" if value else "false"
        else:
            serialized = str(value)
        pairs.append((str(key), serialized))

    if not pairs:
        return url
    return f"{url}?{urllib.parse.urlencode(pairs)}"


def _request(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = _build_url(path, params)
    headers = {
        "Authorization": f"Bearer {_get_mcp_api_key()}",
        "User-Agent": "SLO-MCP-Server/1.0",
    }
    data: bytes | None = None

    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(url=url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
            if raw.strip() == "":
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"text": raw}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request failed: {exc.reason}") from exc


@mcp.tool()
def tree(depth: int | None = None, path_prefix: str = "") -> dict[str, Any]:
    """Return the vault tree view as text, optionally limited by depth and path prefix."""
    return _request("GET", "/tree", {"depth": depth, "path_prefix": path_prefix})


@mcp.tool()
def list_docs(path_prefix: str | None = None) -> dict[str, Any]:
    """List active documents with metadata, optionally filtered to a path subtree."""
    return _request("GET", "/docs", {"path_prefix": path_prefix})


@mcp.tool()
def read_doc(path: str) -> dict[str, Any]:
    """Read a single markdown document by vault-relative path and return full content."""
    return _request("GET", "/docs/by-path", {"path": path})


@mcp.tool()
def create_doc(
    path: str,
    content: str = "",
    title: str | None = None,
    create_parents: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create a markdown document at path with optional title/content and parent folder creation."""
    return _request(
        "POST",
        "/docs",
        payload={
            "path": path,
            "content": content,
            "title": title,
            "create_parents": create_parents,
            "overwrite": overwrite,
        },
    )


@mcp.tool()
def update_doc(
    path: str,
    content: str,
    title: str | None = None,
    expected_hash: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update document content by path, with optional optimistic hash check and change reason."""
    return _request(
        "PUT",
        "/docs/by-path",
        {"path": path},
        {
            "content": content,
            "title": title,
            "expected_hash": expected_hash,
            "reason": reason,
        },
    )


@mcp.tool()
def delete_doc(path: str, reason: str | None = None) -> dict[str, Any]:
    """Delete a document by path and move it to trash with an optional deletion reason."""
    payload = {"reason": reason} if reason is not None else None
    return _request("DELETE", "/docs/by-path", {"path": path}, payload)


@mcp.tool()
def move_doc(from_path: str, to_path: str, overwrite: bool = False) -> dict[str, Any]:
    """Move or rename a document from one path to another, optionally overwriting target."""
    source = _request("GET", "/docs/by-path", {"path": from_path})
    doc_id = str(source.get("id", "")).strip()
    if doc_id == "":
        raise RuntimeError("failed to resolve doc_id from from_path")

    return _request(
        "POST",
        "/docs/move",
        payload={
            "doc_id": doc_id,
            "to_path": to_path,
            "overwrite": overwrite,
        },
    )


@mcp.tool()
def search(
    q: str,
    mode: str = "keyword",
    top_k: int = 20,
    chunk_size: int = 800,
    chunk_overlap: int = 120,
    path_prefix: str | None = None,
) -> dict[str, Any]:
    """Search document chunks by query with keyword/vector mode and optional subtree filter."""
    return _request(
        "GET",
        "/search",
        {
            "q": q,
            "mode": mode,
            "top_k": top_k,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "path_prefix": path_prefix,
        },
    )


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise ValueError("MCP_TRANSPORT must be stdio, sse, or streamable-http")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
