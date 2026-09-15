"""
Write tests/ground_truth.lock: sha256 of every generated text file the evals depend on.

tests/test_ground_truth_lock.py fails if `make synth` output drifts from the lock, so a generator
change can never silently move the answer key. Re-run this ONLY when the change is intended, and
record why in docs/decisions.md.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"
LOCK = REPO_ROOT / "tests" / "ground_truth.lock"
# manifest.json and DATASET.md carry a generation timestamp; parquet bytes depend on the pyarrow version
PATTERNS = ("ground_truth/*.json", "sources/*/*.csv", "master/*.csv", "report_feed/*.csv", "kb/**/*.md",
            "kb/**/*.json")
EXCLUDE = {"ground_truth/manifest.json"}


def current_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for pattern in PATTERNS:
        for path in DATA.glob(pattern):
            rel = path.relative_to(DATA).as_posix()
            if rel not in EXCLUDE:
                hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(hashes.items()))


def read_lock() -> dict[str, str]:
    lines = LOCK.read_text(encoding="utf-8").splitlines()
    return dict(reversed(line.split("  ", 1)) for line in lines if line.strip())  # type: ignore[misc]


def main() -> int:
    hashes = current_hashes()
    if not hashes:
        print("no generated data found; run `make synth` first")
        return 1
    LOCK.write_text("".join(f"{h}  {rel}\n" for rel, h in hashes.items()), encoding="utf-8", newline="\n")
    print(f"locked {len(hashes)} files -> {LOCK.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
