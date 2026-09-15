from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from pathlib import Path

import pytest

from app.config import REPO_ROOT
from retrieval.chunker import MAX_WORDS, ChunkError, blocks, chunk_corpus, chunk_document, pieces
from retrieval.index import StaleIndexError, build, load

KB = REPO_ROOT / "data" / "kb"
pytestmark = pytest.mark.skipif(not (KB / "docs").is_dir(), reason="data/ not generated (run `make synth`)")

FRONTMATTER = """---
doc_id: {doc_id}
title: Test document
doc_type: sop
version: 2.0
effective_date: 2026-01-15
status: current
access_level: plant
plant: PLT-02
supersedes: None
superseded_by: None
audience: both
challenges: ["C7"]
---
"""


def _doc(tmp_path: Path, body: str, doc_id: str = "SOP-TEST-001") -> Path:
    (tmp_path / "docs").mkdir(exist_ok=True)
    path = tmp_path / "docs" / f"{doc_id}.md"
    path.write_text(FRONTMATTER.format(doc_id=doc_id) + body, encoding="utf-8")
    return path


def _tables(text: str) -> list[list[str]]:
    return [b.split("\n") for b in blocks(text) if b.lstrip().startswith("|")]


# --- the real corpus -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus() -> list:
    return chunk_corpus(KB)


def test_every_document_and_resolution_is_chunked_with_its_frontmatter(corpus: list) -> None:
    docs = sorted(p.stem for p in (KB / "docs").glob("*.md"))
    resolutions = json.loads((KB / "resolutions" / "prior_resolutions.json").read_text(encoding="utf-8"))
    assert sorted({c.doc_id for c in corpus if c.doc_type != "prior_resolution"}) == docs  # 26
    assert len([c for c in corpus if c.doc_type == "prior_resolution"]) == len(resolutions) == 93
    for c in corpus:
        assert c.doc_id and c.version and c.status in ("current", "superseded", "expired")
        assert c.access_level in ("all", "plant", "restricted") and re.fullmatch(r"\d{4}-\d{2}-\d{2}",
                                                                                 c.effective_date)
        assert (c.access_level == "plant") == (c.plant is not None) or c.doc_type == "prior_resolution"
        assert "challenges" not in c.text and "C7" not in c.section


def test_metadata_matches_the_frontmatter_it_came_from(corpus: list) -> None:
    by_id = {c.chunk_id: c for c in corpus}
    v1 = by_id["SOP-DQ-001-v1#0"]
    assert (v1.status, v1.superseded_by, v1.version, v1.effective_date) == (
        "superseded", "SOP-DQ-001-v2", "1.0", "2025-03-01")
    plant = by_id["PLANT-PLT-02#0"]
    assert (plant.access_level, plant.plant) == ("plant", "PLT-02")
    assert {c.access_level for c in corpus if c.doc_id in ("SLA-C000031", "POL-ACC-001")} == {"restricted"}
    assert {c.status for c in corpus if c.doc_id == "MEMO-2025-07"} == {"expired"}


def test_no_table_row_is_separated_from_its_header(corpus: list) -> None:
    source_tables = {}
    for path in (KB / "docs").glob("*.md"):
        for table in _tables(path.read_text(encoding="utf-8")):
            source_tables[table[0]] = table
    seen = 0
    for c in corpus:
        for table in _tables(c.text):
            assert table[0] in source_tables, f"{c.chunk_id}: a table piece starts without its header row"
            assert re.fullmatch(r"\|[\s:|-]+\|", table[1].strip()), f"{c.chunk_id}: header separator missing"
            seen += 1
    assert seen >= len(source_tables) > 0


def test_sections_are_chunks_and_the_preamble_banner_is_kept(corpus: list) -> None:
    md = [c for c in corpus if c.doc_id == "MD-001"]
    assert [c.section for c in md] == ["", "1. Scope", "2. On time", "3. In full", "4. On time in full",
                                       "5. Fill rate", "6. Scope exclusions", "7. Version history"]
    assert "SUPERSEDED" in next(c.text for c in corpus if c.chunk_id == "SOP-DQ-001-v1#0")
    assert all(len(c.text.split()) <= MAX_WORDS + 20 for c in corpus)  # + the title/section label


def test_answer_content_is_never_split_across_chunks(corpus: list) -> None:
    """For each golden question with an expected document, all of its required terms sit in one chunk of an
    expected document — a question answerable from the corpus is answerable from a single chunk."""
    questions = json.loads((REPO_ROOT / "data" / "ground_truth" / "kb_questions.json")
                           .read_text(encoding="utf-8"))["questions"]
    texts = defaultdict(list)
    for c in corpus:
        texts[c.doc_id].append(c.text.lower())
    checked = 0
    for q in questions:
        if not q["expected_doc_ids"] or not q["must_contain"]:
            continue
        candidates = [t for d in q["expected_doc_ids"] for t in texts[d]]
        terms = [m.lower() for m in q["must_contain"]]
        assert any(all(t in text for t in terms) for text in candidates), q["question_id"]
        checked += 1
    assert checked >= 30


# --- splitting rules on constructed documents ------------------------------------------------


def test_a_long_section_splits_between_paragraphs_and_a_long_table_repeats_its_header(tmp_path: Path) -> None:
    para = " ".join(["word"] * 30)
    rows = "\n".join(f"| G{i} | {i}.0% | plus or minus 1.5 pts |" for i in range(40))
    body = (f"# Title\n\nBanner line.\n\n## Long section\n\n{para}\n\n{para}\n\n{para}\n\n"
            f"| Group | Yield | Tolerance |\n|---|---|---|\n{rows}\n\n## Short\n\nDone.\n")
    chunks = chunk_document(_doc(tmp_path, body), tmp_path, budget=60)
    long = [c for c in chunks if c.section == "Long section"]
    assert len(long) > 3
    for c in long:
        for table in _tables(c.text):
            assert table[:2] == ["| Group | Yield | Tolerance |", "|---|---|---|"] and len(table) > 2
    table_rows = [r for c in long for t in _tables(c.text) for r in t[2:]]
    assert table_rows == rows.split("\n")  # every row once, in order
    assert [c.chunk_id for c in chunks] == [f"SOP-TEST-001#{i}" for i in range(len(chunks))]
    assert chunks[0].section == "" and chunks[-1].section == "Short"


def test_a_table_directly_under_a_paragraph_is_its_own_block() -> None:
    text = "Intro line.\n| A | B |\n|---|---|\n| 1 | 2 |\nAfter."
    assert blocks(text) == ["Intro line.", "| A | B |\n|---|---|\n| 1 | 2 |", "After."]
    assert pieces(text, budget=1000) == ["Intro line.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nAfter."]


def test_subsection_headings_build_a_path(tmp_path: Path) -> None:
    body = "# Title\n\n## Part A\n\nText a.\n\n### Detail\n\nText d.\n\n## Part B\n\nText b.\n"
    sections = [c.section for c in chunk_document(_doc(tmp_path, body), tmp_path)]
    assert sections == ["Part A", "Part A > Detail", "Part B"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("no frontmatter here", "no frontmatter"),
        (FRONTMATTER.format(doc_id="OTHER-ID") + "# T\n\nx\n", "does not match the file name"),
        (FRONTMATTER.format(doc_id="SOP-TEST-001").replace("status: current", "status: draft") + "x",
         "status"),
    ],
)
def test_bad_documents_are_rejected(tmp_path: Path, text: str, message: str) -> None:
    (tmp_path / "docs").mkdir()
    path = tmp_path / "docs" / "SOP-TEST-001.md"
    path.write_text(text, encoding="utf-8")
    with pytest.raises((ChunkError, ValueError), match=message):
        chunk_document(path, tmp_path)


# --- the index -------------------------------------------------------------------------------


def test_index_build_is_deterministic_and_refuses_a_stale_corpus(tmp_path: Path) -> None:
    kb = tmp_path / "kb"
    shutil.copytree(KB, kb)
    first = build(kb, tmp_path / "index")
    snapshot = (tmp_path / "index" / "chunks.jsonl").read_bytes()
    assert build(kb, tmp_path / "index") == first
    assert (tmp_path / "index" / "chunks.jsonl").read_bytes() == snapshot
    assert (first.documents, first.resolutions, first.chunks) == (26, 93, len(load(tmp_path / "index", kb)))
    doc = kb / "docs" / "SOP-DQ-001-v2.md"
    doc.write_text(doc.read_text(encoding="utf-8") + "\n## Added\n\nNew text.\n", encoding="utf-8")
    with pytest.raises(StaleIndexError, match="changed since"):
        load(tmp_path / "index", kb)
    with pytest.raises(StaleIndexError, match="make index"):
        load(tmp_path / "missing")
