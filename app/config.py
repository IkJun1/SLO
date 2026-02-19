from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    api_prefix: str
    vault_root: Path
    db_path: Path
    faiss_index_path: Path
    embedding_provider: str
    embedding_api_base: str
    embedding_model: str
    embedding_batch_size: int
    llm_api_base: str
    llm_model: str


_settings_cache: Settings | None = None
_dotenv_cache: dict[str, str] | None = None


def _load_dotenv_values() -> dict[str, str]:
    global _dotenv_cache
    if _dotenv_cache is not None:
        return _dotenv_cache

    values: dict[str, str] = {}
    dotenv_path = Path(__file__).resolve().parent.parent / ".env"
    if dotenv_path.exists() and dotenv_path.is_file():
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line == "" or line.startswith("#"):
                continue
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            if key == "":
                continue

            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]

            values[key] = value

    _dotenv_cache = values
    return values


def _raw_setting(name: str) -> str | None:
    from_env = os.getenv(name)
    if from_env is not None:
        return from_env.strip()

    from_dotenv = _load_dotenv_values().get(name)
    if from_dotenv is None:
        return None
    return from_dotenv.strip()


def _path_from_env(name: str, default: Path) -> Path:
    raw = _raw_setting(name)
    if raw:
        return Path(raw).expanduser().resolve()
    return default.expanduser().resolve()


def get_settings() -> Settings:
    global _settings_cache
    if _settings_cache is None:
        project_root = Path(__file__).resolve().parent.parent
        provider = "ollama"

        embedding_api_base = _raw_setting("SLO_EMBEDDING_API_BASE") or "http://localhost:11434"
        embedding_model = _raw_setting("SLO_EMBEDDING_MODEL") or "bge-m3:567m"
        embedding_batch_size = 16
        llm_api_base = _raw_setting("SLO_LLM_API_BASE") or "http://localhost:11434"
        llm_model = _raw_setting("SLO_LLM_MODEL") or "gemma3:270m"

        if (_raw_setting("SLO_EMBEDDING_API_BASE") or "") == "":
            embedding_api_base = llm_api_base

        _settings_cache = Settings(
            api_prefix="/api/v1",
            vault_root=(project_root / "vault").resolve(),
            db_path=Path("./data/slo.db").expanduser().resolve(),
            faiss_index_path=Path("./data/slo.faiss").expanduser().resolve(),
            embedding_provider=provider,
            embedding_api_base=embedding_api_base,
            embedding_model=embedding_model,
            embedding_batch_size=embedding_batch_size,
            llm_api_base=llm_api_base,
            llm_model=llm_model,
        )
    return _settings_cache


def ensure_runtime_paths(settings: Settings) -> None:
    settings.vault_root.mkdir(parents=True, exist_ok=True)
    (settings.vault_root / ".trash").mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.faiss_index_path.parent.mkdir(parents=True, exist_ok=True)
