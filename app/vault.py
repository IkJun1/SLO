from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from app.errors import APIError


def normalize_rel_path(path: str, *, allow_empty: bool = False) -> str:
    raw = (path or "").strip().replace("\\", "/")
    raw = raw.strip("/")

    if raw == "":
        if allow_empty:
            return ""
        raise APIError("INVALID_INPUT", 400, "path is required")

    pure = PurePosixPath(raw)
    if pure.is_absolute():
        raise APIError("PATH_OUT_OF_VAULT", 400, "absolute path is not allowed", {"path": path})

    invalid_parts = {"", ".", ".."}
    if any(part in invalid_parts for part in pure.parts):
        raise APIError("PATH_OUT_OF_VAULT", 400, "path must stay inside vault", {"path": path})

    return pure.as_posix()


def _ensure_within_root(root: Path, target: Path) -> None:
    if not target.is_relative_to(root):
        raise APIError("PATH_OUT_OF_VAULT", 400, "path must stay inside vault")


def _ensure_no_symlink(root: Path, rel_path: str) -> None:
    if root.exists() and root.is_symlink():
        raise APIError("SYMLINK_BLOCKED", 400, "vault root cannot be a symlink")

    current = root
    if rel_path:
        for part in PurePosixPath(rel_path).parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise APIError("SYMLINK_BLOCKED", 400, "symlink path is blocked", {"path": rel_path})


def resolve_vault_path(root: Path, rel_path: str, *, allow_empty: bool = False) -> Path:
    normalized = normalize_rel_path(rel_path, allow_empty=allow_empty)
    resolved_root = root.resolve()
    candidate = (resolved_root / normalized).resolve()
    _ensure_within_root(resolved_root, candidate)
    _ensure_no_symlink(resolved_root, normalized)
    return candidate


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".md", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            _ = handle.write(content)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".bin", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            _ = handle.write(data)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def move_path(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.move(str(source), str(target))


def move_to_trash(vault_root: Path, source: Path) -> str:
    now = datetime.now(timezone.utc)
    trash_dir = vault_root / ".trash" / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d")
    trash_dir.mkdir(parents=True, exist_ok=True)

    candidate = trash_dir / f"{uuid4().hex[:8]}-{source.name}"
    move_path(source, candidate)
    return candidate.relative_to(vault_root).as_posix()


def render_tree(base_path: Path, depth: int | None = None) -> str:
    if depth is not None and depth < 0:
        raise APIError("INVALID_INPUT", 400, "depth must be >= 0")

    lines = ["."]
    dir_count = 0
    file_count = 0

    def list_entries(current: Path) -> list[Path]:
        entries = [entry for entry in current.iterdir() if entry.name not in {".trash", ".git"}]
        for entry in entries:
            if entry.is_symlink():
                raise APIError("SYMLINK_BLOCKED", 400, "symlink path is blocked", {"path": str(entry)})
        entries.sort(key=lambda item: (not item.is_dir(), item.name.lower()))
        return entries

    def walk(current: Path, prefix: str, level: int) -> None:
        nonlocal dir_count, file_count
        if depth is not None and level >= depth:
            return

        entries = list_entries(current)
        for idx, entry in enumerate(entries):
            last = idx == len(entries) - 1
            connector = "`-- " if last else "|-- "
            is_dir = entry.is_dir()

            lines.append(f"{prefix}{connector}{entry.name}{'/' if is_dir else ''}")

            if is_dir:
                dir_count += 1
                child_prefix = f"{prefix}{'    ' if last else '|   '}"
                walk(entry, child_prefix, level + 1)
            else:
                file_count += 1

    walk(base_path, "", 0)
    lines.append("")
    lines.append(f"{dir_count} directories, {file_count} files")
    return "\n".join(lines)
