"""Score the A13 exception agent. Equivalent of `make eval-agent`.

Runs against a separate evaluation database (default m3tdf_agent_eval on the DATABASE_URL server) holding all
three plants, because the answer-key patterns span plants. `--build` (re)creates it: migrate, confirm the
heuristic mapping for every header, ingest every file, and index the knowledge base (Voyage). Then it samples
exceptions per pattern (evals/cases/agent_outcomes.yaml), runs the agent on each up to the approval interrupt
(no decision is recorded: the eval scores classification, not approval), and writes
evals/results/agent_<date>.json. Real model calls: roughly 4 per run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import Engine, create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from agent.graph import AgentDeps, start_run  # noqa: E402
from app.config import get_settings  # noqa: E402
from db.migrate import MigrationError, migrate, require_postgres  # noqa: E402
from evals.agent_eval import AgentResult, AgentScore, samples  # noqa: E402
from llm.client import DbRecorder, build_client  # noqa: E402
from llm.embeddings import embedder_for  # noqa: E402
from pipeline.ingest import ingest_file  # noqa: E402
from pipeline.mapper.heuristic import propose  # noqa: E402
from pipeline.profile import discover_sources, profile_all  # noqa: E402
from retrieval.store import sync  # noqa: E402


def build(server_url: str, name: str, data_dir: Path) -> None:
    admin = create_engine(make_url(server_url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    url = make_url(server_url).set(database=name).render_as_string(hide_password=False)
    migrate(url)
    engine = create_engine(url)
    profiles = profile_all(data_dir / "sources", data_dir / "master")
    for profile in profiles:
        for variant in profile.header_variants:
            proposal = propose(profile, variant.columns)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO ops.mapping_versions (source, version, mapping, proposed_by, status, "
                        "confirmed_by, confirmed_at, header, header_hash) "
                        "SELECT :s, COALESCE(MAX(version), 0) + 1, CAST(:m AS jsonb), 'heuristic', "
                        "'confirmed', 'eval', now(), CAST(:hdr AS jsonb), :h FROM ops.mapping_versions "
                        "WHERE source = :s"
                    ),
                    {
                        "s": profile.source,
                        "m": json.dumps({"columns": [c.model_dump() for c in proposal.columns]}),
                        "hdr": json.dumps(variant.columns),
                        "h": proposal.header_hash,
                    },
                )
    for source, files in discover_sources(data_dir / "sources").items():
        for f in files:
            result = ingest_file(engine, source, f, data_dir / "master")
            print(f"  {source} {f.name}: {result.status}")
    settings = get_settings()
    synced = sync(engine, data_dir / "kb", embedder_for(settings, DbRecorder(engine)))
    print(f"  knowledge base: {synced.chunks} chunks, {synced.embedded} embedded")
    engine.dispose()


def _write(summary: dict) -> Path:
    out = REPO_ROOT / "evals" / "results" / f"agent_{date.today().isoformat()}.json"
    out.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    return out


def _llm_errors(engine: Engine, since: datetime) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM ops.llm_calls WHERE purpose LIKE 'agent_%' AND outcome = 'error' "
                    "AND called_at >= :t"
                ),
                {"t": since},
            ).scalar_one()
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="m3tdf_agent_eval")
    parser.add_argument("--build", action="store_true", help="(re)create and load the evaluation database")
    parser.add_argument("--per-pattern", type=int, default=3)
    parser.add_argument(
        "--coverage-limit", type=int, default=None, help="default: all 17 coverage-gate faults"
    )
    parser.add_argument("--pace", type=float, default=5.0, help="seconds between runs (rate limits)")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many runs (smoke test)")
    args = parser.parse_args()
    settings = get_settings()
    try:
        require_postgres(settings.database_url)
    except MigrationError as exc:
        print(f"eval-agent: {exc}", file=sys.stderr)
        return 1
    url = make_url(settings.database_url).set(database=args.database).render_as_string(hide_password=False)
    if args.build:
        print(f"building {args.database} ...")
        build(settings.database_url, args.database, settings.data_dir)
    engine = create_engine(url)
    recorder = DbRecorder(engine)
    llm = build_client(settings, recorder)
    if llm is None:
        print("eval-agent: LLM_PROVIDER is not configured", file=sys.stderr)
        return 1
    deps = AgentDeps(engine, llm, settings.data_dir / "master", embedder_for(settings, recorder))
    chosen = samples(engine, settings.data_dir, args.per_pattern, args.coverage_limit)[: args.limit]
    print(f"{len(chosen)} runs ({sum(s.coverage for s in chosen)} coverage-gate)")
    results: list[AgentResult] = []
    for i, sample in enumerate(chosen, 1):
        started = datetime.now(UTC)
        try:
            view = start_run(deps, url, sample.exception_id, "eval")
            resolution = view.resolution or {}
            result = AgentResult(
                sample,
                view.run_id,
                resolution.get("outcome"),
                resolution.get("confidence"),
                resolution.get("not_covered"),
                resolution.get("proposed_fix") is not None,
                (view.gate or {}).get("disposition"),
                len([s for s in view.steps if s.get("tool")]),
                [e["kind"] for e in resolution.get("evidence_refs", [])],
                view.fallback,
                _llm_errors(engine, started),
            )
        except Exception as exc:  # a failed run is reported, not dropped
            result = AgentResult(
                sample,
                None,
                None,
                None,
                None,
                False,
                None,
                0,
                [],
                False,
                _llm_errors(engine, started),
                f"{type(exc).__name__}: {exc}"[:500],
            )
        results.append(result)
        mark = "ok " if result.correct else "ERR" if result.error else "no "
        print(
            f"[{i}/{len(chosen)}] {mark} {sample.case:<28} {sample.plant} exc {sample.exception_id}: "
            f"{result.outcome} (expected {sample.expected}{', not_covered' if sample.coverage else ''}) "
            f"tools {result.tool_calls} gate {result.gate}{' FALLBACK' if result.fallback else ''}"
            f"{f' llm_errors {result.llm_errors}' if result.llm_errors else ''}"
            f"{f' {result.error}' if result.error else ''}"
        )
        _write(AgentScore(results).as_dict())  # after every run: an interrupted eval keeps what it measured
        if args.pace and i < len(chosen):
            time.sleep(args.pace)
    engine.dispose()

    summary = AgentScore(results).as_dict()
    print(
        f"\naccuracy {summary['accuracy']} (lenient {summary['lenient_accuracy']}), target met "
        f"{summary['meets_accuracy_target']}"
    )
    for rule, s in summary["per_rule"].items():
        print(f"  {rule}: {s['accuracy']} of {s['n']} {s['outcomes']}")
    gate = summary["coverage_gate"]
    print(
        f"COVERAGE GATE {gate['passed']}/{gate['n']} — hard gate met: {gate['hard_gate_met']} "
        f"{gate['outcomes']}"
    )
    print(
        f"evidence quality {summary['evidence_quality']}, mean tool calls {summary['mean_tool_calls']}, "
        f"hit cap {summary['hit_tool_cap']}, escalations on auto_fixable patterns "
        f"{summary['escalations_on_auto_fixable_patterns']}, fixes proposed {summary['proposed_fixes']} "
        f"{summary['gate_dispositions']}, fallbacks {summary['fallbacks']}, excluded (transport errors) "
        f"{summary['excluded_transport']}, failed runs {summary['runs_failed']}"
    )
    out = _write(summary)
    print(f"wrote {out.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
