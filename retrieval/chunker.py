"""Section-level chunking of the knowledge base (docs/addendum1.md A14).

Documents in data/kb/docs/*.md split on markdown headings. The text between the title and the first section
(a version or SUPERSEDED banner) is a chunk of its own. A section longer than MAX_WORDS splits between
paragraphs, never inside a table; a table too long for one chunk is cut into row groups that each repeat the
header row, so a table row is never separated from its header. Every chunk carries its document's frontmatter
(doc_id, version, status, access_level, plant, effective_date, ...) except `challenges`: a generator label
naming the test a document was written for, which would leak the answer key into retrieval.

Prior exception resolutions (data/kb/resolutions/prior_resolutions.json) are one chunk each.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

MAX_WORDS = 350
CHUNKER_VERSION = 1
GENERATOR_ONLY = frozenset({"challenges"})  # frontmatter describing the test, not the document

Status = Literal["current", "superseded", "expired"]
Access = Literal["all", "plant", "restricted"]


class DocMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    title: str
    doc_type: str
    version: str
    effective_date: date
    status: Status
    access_level: Access
    plant: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    audience: str

    @field_validator("plant", "supersedes", "superseded_by", mode="before")
    @classmethod
    def _none(cls, value: object) -> object:
        return None if value in (None, "", "None", "null") else value

    @field_validator("version", mode="before")
    @classmethod
    def _text(cls, value: object) -> str:
        return str(value)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str  # "<doc_id>#<n>"
    doc_id: str
    title: str
    section: str  # heading path below the title; "" for the preamble
    text: str  # what is searched and shown: title and section label, then the body
    doc_type: str
    version: str
    effective_date: str
    status: Status
    access_level: Access
    plant: str | None
    supersedes: str | None
    superseded_by: str | None
    audience: str
    source_path: str

    def as_dict(self) -> dict:
        return asdict(self)


class ChunkError(ValueError):
    pass


def parse_document(path: Path) -> tuple[DocMeta, str]:
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if match is None:
        raise ChunkError(f"{path.name}: no frontmatter block")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            raise ChunkError(f"{path.name}: frontmatter line without a key: {line!r}")
        fields[key.strip()] = value.strip()
    meta = DocMeta.model_validate({k: v for k, v in fields.items() if k not in GENERATOR_ONLY})
    if path.stem != meta.doc_id:
        raise ChunkError(f"{path.name}: doc_id {meta.doc_id} does not match the file name")
    return meta, match.group(2)


def _is_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def blocks(body: str) -> list[str]:
    """Paragraphs separated by blank lines, with every markdown table kept as one block of its own."""
    out: list[list[str]] = []
    current: list[str] = []
    for line in body.split("\n"):
        if not line.strip():
            if current:
                out.append(current)
                current = []
            continue
        if current and _is_row(line) != _is_row(current[-1]):  # table starts or ends without a blank line
            out.append(current)
            current = []
        current.append(line)
    if current:
        out.append(current)
    return ["\n".join(b) for b in out]


def _words(text: str) -> int:
    return len(text.split())


def _split_table(table: str, budget: int) -> list[str]:
    rows = table.split("\n")
    head, body = rows[:2], rows[2:]  # the header row and its |---| separator travel with every piece
    pieces, current = [], list(head)
    for row in body:
        if len(current) > 2 and _words("\n".join([*current, row])) > budget:
            pieces.append("\n".join(current))
            current = list(head)
        current.append(row)
    pieces.append("\n".join(current))
    return pieces


def pieces(body: str, budget: int = MAX_WORDS) -> list[str]:
    """Pack blocks into pieces of at most `budget` words, splitting only between blocks (or table rows)."""
    out: list[str] = []
    current: list[str] = []
    for block in blocks(body):
        parts = _split_table(block, budget) if _is_row(block) and _words(block) > budget else [block]
        for part in parts:
            if current and _words("\n\n".join([*current, part])) > budget:
                out.append("\n\n".join(current))
                current = []
            current.append(part)
    if current:
        out.append("\n\n".join(current))
    return out


def chunk_document(path: Path, root: Path, budget: int = MAX_WORDS) -> list[Chunk]:
    meta, body = parse_document(path)
    sections: list[tuple[str, str, str]] = []  # (title, heading path, body)
    title, stack, heading, lines = meta.title, [], "", []  # type: ignore[var-annotated]
    for line in body.split("\n"):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m is None:
            lines.append(line)
            continue
        sections.append((title, heading, "\n".join(lines)))
        level, text = len(m.group(1)), m.group(2).strip()
        if level == 1:
            title, stack = text, []
        else:
            stack = [*stack[: level - 2], text]
        heading, lines = " > ".join(stack), []
    sections.append((title, heading, "\n".join(lines)))

    common = meta.model_dump(exclude={"title", "doc_id", "effective_date"})
    chunks: list[Chunk] = []
    for section_title, section, text in sections:
        if not text.strip():
            continue
        label = f"{section_title} > {section}" if section else section_title
        for piece in pieces(text.strip(), budget):
            chunks.append(Chunk(
                chunk_id=f"{meta.doc_id}#{len(chunks)}", doc_id=meta.doc_id, title=section_title,
                section=section, text=f"{label}\n\n{piece}", effective_date=meta.effective_date.isoformat(),
                source_path=path.relative_to(root).as_posix(), **common))
    if not chunks:
        raise ChunkError(f"{path.name}: no content")
    return chunks


def chunk_resolutions(path: Path, root: Path) -> list[Chunk]:
    """One chunk per prior resolution: the pattern seen, how it was classified and resolved, by whom."""
    chunks = []
    for r in json.loads(path.read_text(encoding="utf-8")):
        rule, _, kind = r["rule_id"].partition("_")
        text = (f"Prior resolution {r['resolution_id']} ({r['plant']}, rule {rule}: {kind.replace('_', ' ')})"
                f"\n\nPattern: {r['pattern']}\nClassification: {r['classification']}\n"
                f"Resolution: {r['resolution']}\nResolved by {r['resolved_by']} on {r['resolved_on']}.")
        chunks.append(Chunk(
            chunk_id=f"{r['resolution_id']}#0", doc_id=r["resolution_id"],
            title=f"Prior resolution {r['resolution_id']}", section="", text=text,
            doc_type="prior_resolution", version="1", effective_date=r["resolved_on"], status="current",
            access_level="all", plant=r["plant"], supersedes=None, superseded_by=None, audience="both",
            source_path=path.relative_to(root).as_posix()))
    return chunks


def chunk_corpus(kb_dir: Path) -> list[Chunk]:
    root = kb_dir.parent
    chunks = [c for p in sorted((kb_dir / "docs").glob("*.md")) for c in chunk_document(p, root)]
    resolutions = kb_dir / "resolutions" / "prior_resolutions.json"
    if resolutions.exists():
        chunks += chunk_resolutions(resolutions, root)
    ids = [c.chunk_id for c in chunks]
    if len(set(ids)) != len(ids):
        raise ChunkError("duplicate chunk ids")
    return chunks
