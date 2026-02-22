from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from app.config import Settings
from app.errors import APIError
from app.vault import atomic_write_bytes, move_path, normalize_rel_path, resolve_vault_path


MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024

_ALLOWED_IMAGE_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

_ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


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
    url = f"{settings.api_prefix}/user/images/by-path?path={encoded}"
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
