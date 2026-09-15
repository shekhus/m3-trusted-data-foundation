"""Embedding calls (the model-call counterpart of llm/client.py for vectors).

One provider behind `Embedder`: Voyage AI over its REST API (no extra SDK; httpx is already a dependency).
Every request is recorded to ops.llm_calls (purpose embed_document / embed_query, tokens, latency, cost,
outcome). A 429 rate limit is waited out with backoff (transport, not a changed answer); any other failure
raises. `HashingEmbedder` is a deterministic, offline stand-in for tests; it has no semantic power and is
never used for reported results.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import Callable, Sequence
from typing import Literal, Protocol

import httpx

from app.config import Settings
from llm.client import CallRecord, Recorder

InputType = Literal["document", "query"]
VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
MAX_BATCH = 128  # texts per request; the API allows 1,000, smaller batches keep one failure cheap


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    provider: str
    model: str
    dimensions: int

    def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]: ...


def _hash(texts: Sequence[str], input_type: str) -> str:
    return hashlib.sha256(("\0".join([input_type, *texts])).encode("utf-8")).hexdigest()


class VoyageEmbedder:
    provider = "voyage"

    def __init__(
        self,
        api_key: str,
        model: str,
        dimensions: int,
        recorder: Recorder,
        price_per_mtok: float = 0.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_wait_s: float = 120.0,
    ) -> None:
        if not api_key:
            raise EmbeddingError("VOYAGE_API_KEY is not set (add it to .env, or set EMBEDDING_PROVIDER=none)")
        self.model, self.dimensions = model, dimensions
        self._key, self._recorder, self._price = api_key, recorder, price_per_mtok
        self._client = client or httpx.Client(timeout=60.0)
        self._sleep, self._max_wait = sleep, max_wait_s

    def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH):
            out.extend(self._request(list(texts[start : start + MAX_BATCH]), input_type))
        return out

    def _request(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        body = {
            "input": texts,
            "model": self.model,
            "input_type": input_type,
            "output_dimension": self.dimensions,
            "truncation": False,
        }
        waited, delay = 0.0, 5.0
        while True:
            started = time.monotonic()
            try:
                response = self._client.post(
                    VOYAGE_URL, json=body, headers={"Authorization": f"Bearer {self._key}"}
                )
            except httpx.HTTPError as exc:
                self._record(texts, input_type, started, None, "error", f"transport: {type(exc).__name__}")
                raise EmbeddingError(f"Voyage request failed: {type(exc).__name__}") from exc
            if response.status_code == 429 and waited + delay <= self._max_wait:
                self._record(texts, input_type, started, None, "error", "429 rate limited; waiting")
                self._sleep(delay)
                waited, delay = waited + delay, min(delay * 2, 60.0)
                continue
            if response.status_code != 200:
                detail = response.text[:300]
                self._record(texts, input_type, started, None, "error", f"{response.status_code}: {detail}")
                raise EmbeddingError(f"Voyage returned {response.status_code}: {detail}")
            payload = response.json()
            tokens = int(payload.get("usage", {}).get("total_tokens", 0))
            vectors = [item["embedding"] for item in sorted(payload["data"], key=lambda d: d["index"])]
            if len(vectors) != len(texts) or any(len(v) != self.dimensions for v in vectors):
                self._record(
                    texts, input_type, started, tokens, "invalid_output", "count or dimension mismatch"
                )
                raise EmbeddingError("Voyage returned the wrong number or size of vectors")
            self._record(texts, input_type, started, tokens, "ok", None)
            return vectors

    def _record(
        self,
        texts: list[str],
        input_type: str,
        started: float,
        tokens: int | None,
        outcome: str,
        error: str | None,
    ) -> None:
        cost = None if tokens is None else round(tokens * self._price / 1_000_000, 6)
        self._recorder.record(
            CallRecord(
                purpose=f"embed_{input_type}",
                provider=self.provider,
                model=self.model,
                prompt_hash=_hash(texts, input_type),
                input_tokens=tokens,
                output_tokens=0 if tokens else None,
                latency_ms=int((time.monotonic() - started) * 1000),
                cost_usd=cost,
                outcome=outcome,  # type: ignore[arg-type]
                error=error,
            )
        )


class HashingEmbedder:
    """Offline, deterministic bag-of-words vectors for tests. Not a semantic model; never reported."""

    provider = "hashing"

    def __init__(self, dimensions: int = 64) -> None:
        self.model, self.dimensions = f"hashing-{dimensions}", dimensions
        self.calls = 0

    def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * self.dimensions
            for token in re.findall(r"[a-z0-9]+", t.lower()):
                h = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big")
                v[h % self.dimensions] += 1.0 if h & 1 else -1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


def embedder_for(settings: Settings, recorder: Recorder) -> Embedder | None:
    """The configured embedder, or None when EMBEDDING_PROVIDER=none (lexical retrieval only)."""
    import os

    if settings.embedding_provider == "none":
        return None
    if settings.embedding_provider == "voyage":
        return VoyageEmbedder(
            os.environ.get("VOYAGE_API_KEY", ""),
            settings.embedding_model,
            settings.embedding_dimensions,
            recorder,
            settings.embedding_price,
        )
    raise EmbeddingError(f"unknown EMBEDDING_PROVIDER {settings.embedding_provider!r}")
