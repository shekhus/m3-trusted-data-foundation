from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, _parse_api_keys


def test_api_keys_map_key_to_role() -> None:
    assert _parse_api_keys("viewer:k1, owner:k2") == {"k1": "viewer", "k2": "owner"}


def test_api_keys_skip_malformed_and_empty_pairs() -> None:
    assert _parse_api_keys("nocolon,analyst:,owner:k3") == {"k3": "owner"}


def test_database_url_from_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/x")
    assert Settings().resolved_database_url == "postgresql+psycopg://u:p@db:5432/x"


def test_sqlite_fallback_when_database_url_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    settings = Settings(data_dir=tmp_path / "data")
    assert settings.resolved_database_url == f"sqlite:///{(tmp_path / 'data' / 'm3tdf.sqlite').as_posix()}"
    assert (tmp_path / "data").is_dir()


def test_dotenv_overrides_shell_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.config import _load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nLLM_MODEL=from-dotenv\n\nNOT_A_PAIR\n", encoding="utf-8")
    monkeypatch.setenv("LLM_MODEL", "from-shell")
    _load_dotenv(env_file)
    assert Settings().llm_model == "from-dotenv"
