from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_healthz_ok() -> None:
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
