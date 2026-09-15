"""Runtime settings, read from the environment only (principle: secrets in env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Minimal .env loader so local runs don't need an extra dependency. Env vars win."""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def _parse_api_keys(raw: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    for pair in raw.split(","):
        if ":" not in pair:
            continue
        role, key = pair.split(":", 1)
        if key.strip():
            keys[key.strip()] = role.strip()
    return keys


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / os.environ.get("DATA_DIR", "data"))
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    api_keys: dict[str, str] = field(default_factory=lambda: _parse_api_keys(os.environ.get("API_KEYS", "")))
    llm_provider: str = field(default_factory=lambda: os.environ.get("LLM_PROVIDER", "none"))
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "claude-sonnet-5"))
    llm_price_in: float = field(default_factory=lambda: float(os.environ.get("LLM_PRICE_IN_PER_MTOK", "0")))
    llm_price_out: float = field(default_factory=lambda: float(os.environ.get("LLM_PRICE_OUT_PER_MTOK", "0")))
    source_sla_hours: int = field(default_factory=lambda: int(os.environ.get("SOURCE_SLA_HOURS", "72")))

    @property
    def sources_dir(self) -> Path:
        return self.data_dir / "sources"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'm3tdf.sqlite').as_posix()}"


def get_settings() -> Settings:
    return Settings()
