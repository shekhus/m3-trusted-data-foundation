"""Thin HTTP client for the portal. The portal decides nothing: every permission and rule is the API's."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class Reply:
    ok: bool
    status: int
    data: Any
    error: str | None


class Api:
    def __init__(self, base_url: str, api_key: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=300.0)
        self._headers = {"X-API-Key": api_key}

    def _call(self, method: str, path: str, **kwargs: Any) -> Reply:
        try:
            headers = {**self._headers, **kwargs.pop("headers", {})}
            response = self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            return Reply(False, 0, None, f"API unreachable: {exc}")
        body: Any = response.json() if "json" in response.headers.get("content-type", "") else response.text
        if response.is_success:
            return Reply(True, response.status_code, body, None)
        detail = body.get("detail", body) if isinstance(body, dict) else body
        return Reply(False, response.status_code, None, f"{response.status_code}: {detail}")

    def sources(self) -> Reply:
        return self._call("GET", "/sources")

    def profile(self, source: str) -> Reply:
        return self._call("GET", f"/sources/{source}/profile")

    def upload(self, source: str, name: str, content: bytes) -> Reply:
        return self._call("POST", f"/sources/{source}/files", params={"name": name}, content=content,
                          headers={"Content-Type": "text/csv"})

    def propose(self, source: str) -> Reply:
        return self._call("POST", f"/sources/{source}/mappings/propose")

    def mappings(self, source: str) -> Reply:
        return self._call("GET", f"/sources/{source}/mappings")

    def submit_mapping(self, source: str, header: list[str], columns: list[dict]) -> Reply:
        return self._call("POST", f"/sources/{source}/mappings", json={"header": header, "columns": columns})

    def decide(self, mapping_version_id: int, action: str) -> Reply:
        return self._call("POST", f"/mappings/{mapping_version_id}/{action}")

    def ingest(self, source: str, idempotency_key: str) -> Reply:
        return self._call("POST", f"/ingest/{source}", headers={"Idempotency-Key": idempotency_key})

    def batches(self, source: str) -> Reply:
        return self._call("GET", f"/sources/{source}/batches")
