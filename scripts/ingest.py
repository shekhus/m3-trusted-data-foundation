"""POST /ingest/<source> to the running API with a new Idempotency-Key. `make ingest SRC=plt01`.

Uses the first analyst (else owner) key from API_KEYS, and API_BASE_URL. Pass --key to replay a request.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--key", help="Idempotency-Key to use (default: a new one)")
    parser.add_argument("--api", default=None, help="API base URL (default: API_BASE_URL or localhost:8000)")
    args = parser.parse_args()
    settings = get_settings()
    keys = settings.api_keys.items()
    api_key = next((k for role in ("analyst", "owner") for k, r in keys if r == role), None)
    if api_key is None:
        print("ingest: API_KEYS has no analyst or owner key", file=sys.stderr)
        return 1
    base = args.api or os.environ.get("API_BASE_URL", "http://localhost:8000")
    key = args.key or f"cli-{uuid.uuid4()}"
    print(f"POST {base}/ingest/{args.source}  Idempotency-Key: {key}")
    try:
        response = httpx.post(f"{base}/ingest/{args.source}", timeout=600,
                              headers={"X-API-Key": api_key, "Idempotency-Key": key})
    except httpx.HTTPError as exc:
        print(f"ingest: API unreachable: {exc}", file=sys.stderr)
        return 1
    if not response.is_success:
        print(f"ingest: {response.status_code} {response.text}", file=sys.stderr)
        return 1
    body = response.json()
    replayed = " (replayed)" if response.headers.get("Idempotent-Replayed") else ""
    print(f"{body['validated']} validated, {body['blocked']} blocked, {len(body['stale'])} stale; "
          f"{body['blocking_exceptions']} blocking / {body['warning_exceptions']} warning exceptions"
          f"{replayed}")
    for f in body["files"]:
        print(f"  {f['file_name']}: {f['status']}{' stale' if f['stale'] else ''}"
              f"{' (file replayed)' if f['replayed'] else ''}")
    print(json.dumps({"idempotency_key": key, "attempts": body["attempts"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
