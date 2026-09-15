from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_healthz_ok() -> None:
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_redirects_to_the_api_docs() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).get("/", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/docs"

