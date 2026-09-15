"""Files on disk: generated extracts under data/sources and uploads under data/uploads."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sources.base import Extract, ExtractRef, SourceUnavailable


class FileSystemAdapter:
    def __init__(self, roots: list[Path]) -> None:
        self.roots = roots

    def _files(self, source: str) -> dict[str, Path]:
        found: dict[str, Path] = {}
        for root in self.roots:
            folder = root / source
            if folder.is_dir():
                for path in folder.glob("*.csv"):
                    found.setdefault(path.name, path)
        return found

    def sources(self) -> list[str]:
        names = {d.name for root in self.roots if root.is_dir() for d in root.iterdir() if d.is_dir()}
        return sorted(n for n in names if self._files(n))

    def discover(self, source: str) -> list[ExtractRef]:
        try:
            files = self._files(source)
            return [ExtractRef(source, name, datetime.fromtimestamp(path.stat().st_mtime, UTC))
                    for name, path in sorted(files.items())]
        except OSError as exc:
            raise SourceUnavailable(f"cannot list {source}: {exc}") from exc

    def fetch(self, ref: ExtractRef) -> Extract:
        path = self._files(ref.source).get(ref.name)
        if path is None:
            raise SourceUnavailable(f"{ref.source}/{ref.name} disappeared before it could be read", 60)
        try:
            return Extract(ref, path.read_bytes())
        except OSError as exc:
            raise SourceUnavailable(f"cannot read {ref.source}/{ref.name}: {exc}") from exc
