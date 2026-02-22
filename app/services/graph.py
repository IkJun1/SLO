from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.errors import APIError
from app.models import Document, utc_now
from app.vault import normalize_rel_path


def _iso_timestamp(value: datetime | None = None) -> str:
    target = value or datetime.now(timezone.utc)
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return target.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _request_meta() -> dict[str, str]:
    return {
        "request_id": f"req_{uuid4().hex[:12]}",
        "timestamp": _iso_timestamp(),
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
