"""Runtime settings, read from the environment only (principle: secrets in env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(env_file: Path = REPO_ROOT / ".env") -> None:
    """Minimal .env loader so local runs don't need an extra dependency.

    The repo's .env wins over variables already in the shell: a developer machine often exports an unrelated
    ANTHROPIC_API_KEY or DATABASE_URL for other tools (docs/decisions.md D-012). Containers and CI never see a
    .env (.dockerignore, .gitignore), so there the real environment is the only source.
    """
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # names are upper-cased: `Voyage_API_Key = ...` in .env still sets VOYAGE_API_KEY on Linux and in CI
        os.environ[key.strip().upper()] = value.strip().strip("'\"")


_load_dotenv()


def normalise_database_url(url: str) -> str:
    """Hosted Postgres (Railway, Heroku-style) hands out `postgres://` or `postgresql://` URLs, for which
    SQLAlchemy would pick psycopg2 (not installed). Use psycopg 3 unless a driver is already named."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


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
    database_url: str = field(
        default_factory=lambda: normalise_database_url(os.environ.get("DATABASE_URL", "")))
    api_keys: dict[str, str] = field(default_factory=lambda: _parse_api_keys(os.environ.get("API_KEYS", "")))
    llm_provider: str = field(default_factory=lambda: os.environ.get("LLM_PROVIDER", "none"))
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "claude-sonnet-5"))
    llm_price_in: float = field(default_factory=lambda: float(os.environ.get("LLM_PRICE_IN_PER_MTOK", "0")))
    llm_price_out: float = field(default_factory=lambda: float(os.environ.get("LLM_PRICE_OUT_PER_MTOK", "0")))
    source_sla_hours: int = field(default_factory=lambda: int(os.environ.get("SOURCE_SLA_HOURS", "72")))
    embedding_provider: str = field(default_factory=lambda: os.environ.get("EMBEDDING_PROVIDER", "voyage"))
    embedding_model: str = field(default_factory=lambda: os.environ.get("EMBEDDING_MODEL", "voyage-4"))
    embedding_dimensions: int = field(
        default_factory=lambda: int(os.environ.get("EMBEDDING_DIMENSIONS", "1024")))
    embedding_price: float = field(
        default_factory=lambda: float(os.environ.get("EMBEDDING_PRICE_PER_MTOK", "0")))

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
