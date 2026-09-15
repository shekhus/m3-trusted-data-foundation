"""Profile data/sources → docs/findings.md + docs/findings.json. Equivalent of `make profile`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from pipeline.profile import profile_all, render_markdown  # noqa: E402

OUT_MD = REPO_ROOT / "docs" / "findings.md"
OUT_JSON = REPO_ROOT / "docs" / "findings.json"


def main() -> int:
    settings = get_settings()
    if not settings.sources_dir.is_dir():
        print(f"profile: {settings.sources_dir} not found; run `make synth` first", file=sys.stderr)
        return 1
    profiles = profile_all(settings.sources_dir, settings.data_dir / "master")
    OUT_MD.write_text(render_markdown(profiles), encoding="utf-8", newline="\n")
    payload = [p.model_dump(mode="json") for p in profiles]
    body = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    OUT_JSON.write_text(body, encoding="utf-8", newline="\n")
    for p in profiles:
        print(f"{p.source}: {p.file_count} files, {p.rows:,} rows, {len(p.findings)} findings")
    print(f"wrote {OUT_MD.relative_to(REPO_ROOT)} and {OUT_JSON.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
