"""Internal data console: upload → findings → propose → confirm → silver. `make portal`.

An honest internal tool, not a product UI. It calls the API with the key you enter; the API decides what your
role may do, so an analyst pressing Confirm sees the API's 403 rather than a hidden button.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from pipeline.canonical import BY_NAME, ORDER_LINE  # noqa: E402
from portal.api import Api, Reply  # noqa: E402

st.set_page_config(page_title="M3 data foundation", layout="wide")


def show_error(reply: Reply) -> None:
    st.error(reply.error or "request failed")


with st.sidebar:
    st.header("Connection")
    base_url = st.text_input("API URL", os.environ.get("API_BASE_URL", "http://localhost:8000"))
    api_key = st.text_input("API key", type="password",
                            help="Your role (viewer, analyst, owner) comes from the key.")
    if not api_key:
        st.info("Enter an API key to begin.")
        st.stop()

api = Api(base_url, api_key)
sources_reply = api.sources()
if not sources_reply.ok:
    show_error(sources_reply)
    st.stop()
sources = [s["source"] for s in sources_reply.data]

st.title("Trusted data foundation — mapping console")
(tab_sources, tab_findings, tab_mappings, tab_batches, tab_exceptions, tab_reconcile, tab_knowledge,
 tab_ops) = st.tabs(["Sources & upload", "Findings", "Mappings", "Batches", "Exceptions", "Reconciliation",
                     "Knowledge", "Ops"])

with tab_sources:
    st.dataframe(pd.DataFrame(sources_reply.data), hide_index=True, width="stretch")
    st.subheader("Upload an extract")
    with st.form("upload", clear_on_submit=True):
        target = st.text_input("Source name", placeholder="plt04")
        file = st.file_uploader("CSV file", type=["csv"])
        if st.form_submit_button("Upload") and file is not None:
            reply = api.upload(target.strip(), file.name, file.getvalue())
            if reply.ok:
                note = " (identical file already present)" if reply.data["already_present"] else ""
                stored = reply.data
                st.success(f"Stored {stored['file_name']} with {len(stored['columns'])} columns{note}.")
            else:
                show_error(reply)

source = st.sidebar.selectbox("Source", sources) if sources else None
if source is None:
    st.stop()

with tab_findings:
    reply = api.profile(source)
    if not reply.ok:
        show_error(reply)
    else:
        p = reply.data
        st.caption(f"{p['file_count']} files, {p['rows']:,} rows. Inferred from the files alone.")
        for finding in p["findings"]:
            st.markdown(f"- {finding}")
        st.dataframe(pd.DataFrame([{
            "column": c["name"], "files": c["present_in_files"], "type": c["dtype"], "blank %": c["null_pct"],
            "space %": c["whitespace_pct"], "top shape": c["shapes"][0]["shape"] if c["shapes"] else "",
            "date": c["date"]["format"] if c["date"] else "", "unit": (c["unit"] or {}).get("unit") or "",
            "master": (c["reference"] or {}).get("master") or "",
        } for c in p["columns"]]), hide_index=True, width="stretch")

with tab_mappings:
    if st.button("Propose mappings", help="Heuristic always; LLM too when the API has it enabled."):
        reply = api.propose(source)
        if reply.ok:
            for status in reply.data["llm"]:
                if status["needs_review"]:
                    st.warning(f"Header {status['header_hash'][:10]}: " + "; ".join(status["notes"]))
            st.success(f"{len(reply.data['versions'])} proposal version(s) stored.")
        else:
            show_error(reply)

    versions_reply = api.mappings(source)
    if not versions_reply.ok:
        show_error(versions_reply)
        st.stop()
    versions = versions_reply.data
    headers = {v["header_hash"]: v["header"] for v in versions}
    if not versions:
        st.info("No mappings yet. Press Propose mappings.")
    for hash_, header in headers.items():
        group = [v for v in versions if v["header_hash"] == hash_]
        confirmed = next((v for v in group if v["status"] == "confirmed"), None)
        label = f"Header {hash_[:10]} — {len(header)} columns — " + (
            f"confirmed v{confirmed['version']}" if confirmed else "no confirmed mapping")
        with st.expander(label, expanded=confirmed is None):
            st.code(", ".join(header))
            summary = pd.DataFrame([{"version": v["version"], "by": v["proposed_by"], "status": v["status"],
                                     "unmapped required": ", ".join(v["unmapped_required"])} for v in group])
            st.dataframe(summary, hide_index=True, width="stretch")
            choice = st.selectbox("Version", [v["version"] for v in group], index=len(group) - 1,
                                  key=f"version-{hash_}")
            chosen = next(v for v in group if v["version"] == choice)
            table = pd.DataFrame([{"source column": c["source_col"], "canonical": c["canonical_col"] or "",
                                   "transforms": ", ".join(c["transforms"]), "confidence": c["confidence"],
                                   "rationale": c["rationale"]} for c in chosen["columns"]])
            edited = st.data_editor(
                table, hide_index=True, width="stretch", key=f"editor-{hash_}-{choice}",
                disabled=["source column", "confidence", "rationale"],
                column_config={"canonical": st.column_config.SelectboxColumn(
                    options=["", *[f.name for f in ORDER_LINE]], help="blank = unmapped")})
            left, middle, right = st.columns(3)
            if left.button("Submit as correction", key=f"submit-{hash_}-{choice}"):
                columns = []
                for _, row in edited.iterrows():
                    canonical = row["canonical"] or None
                    transforms = [t.strip() for t in str(row["transforms"]).split(",") if t.strip()]
                    columns.append({"source_col": row["source column"], "canonical_col": canonical,
                                    "confidence": 1.0 if canonical else 0.0, "rationale": "set by a person",
                                    "transforms": transforms if canonical else []})
                reply = api.submit_mapping(source, header, columns)
                if reply.ok:
                    st.success(f"Stored as v{reply.data['version']}.")
                else:
                    show_error(reply)
            if chosen["status"] == "proposed":
                if middle.button("Confirm", type="primary", key=f"confirm-{hash_}-{choice}"):
                    reply = api.decide(chosen["mapping_version_id"], "confirm")
                    if reply.ok:
                        st.success("Confirmed.")
                    else:
                        show_error(reply)
                if right.button("Reject", key=f"reject-{hash_}-{choice}"):
                    reply = api.decide(chosen["mapping_version_id"], "reject")
                    if reply.ok:
                        st.success("Rejected.")
                    else:
                        show_error(reply)
    st.caption("Canonical columns: " + ", ".join(f"{f.name}{' *' if f.required else ''}" for f in ORDER_LINE)
               + ". * required. " + BY_NAME["customer_no"].description + ".")

with tab_batches:
    # one key per click: a double-submit or a retried click replays instead of ingesting twice
    key_name = f"ingest-key-{source}"
    if key_name not in st.session_state:
        st.session_state[key_name] = f"portal-{uuid.uuid4()}"
    ingest_help = "Every file of this source; blocked where a header has no confirmed mapping."
    if st.button("Ingest (bronze → silver → validate)", help=ingest_help):
        reply = api.ingest(source, st.session_state[key_name])
        st.session_state[key_name] = f"portal-{uuid.uuid4()}"
        if reply.ok:
            r = reply.data
            st.success(f"{r['validated']} of {len(r['files'])} files validated; {r['blocked']} blocked; "
                       f"{r['blocking_exceptions']} blocking and {r['warning_exceptions']} warning "
                       "exceptions.")
            if r["stale"]:
                st.warning(f"Stale extracts (older than the source SLA): {', '.join(r['stale'])}")
        else:
            show_error(reply)
    if st.button("Publish to gold (owner)", help="Refuses stale batches and shapes with an open drift alert; "
                                                 "holds rows with blocking exceptions."):
        reply = api.publish(source)
        if reply.ok:
            r = reply.data
            st.success(f"{r['published']} batches published ({r['rows_published']:,} rows), "
                       f"{r['refused']} refused. "
                       f"Gold now holds {r['gold_rows_for_source']:,} rows for {source}.")
            if r["rows_held"]:
                st.caption("Rows held back: " + ", ".join(f"{k}: {v}" for k, v in r["rows_held"].items()))
            refused = [b for b in r["batches"] if b["status"] == "refused"]
            if refused:
                listed = "; ".join(f"{b['file_name']} ({', '.join(b['reasons'])})" for b in refused)
                st.warning(f"Refused: {listed}")
        else:
            show_error(reply)
    drift_reply = api.drift_alerts(source)
    if drift_reply.ok and drift_reply.data:
        st.subheader("Open drift alerts (publish blocked)")
        for alert in drift_reply.data:
            with st.container(border=True):
                st.markdown(f"**Alert {alert['alert_id']}** on {alert['file_name']}: {alert['message']}")
                if not alert["header_has_confirmed_mapping"]:
                    st.caption("Confirm a mapping for the new header (Mappings tab) before resolving.")
                with st.form(f"drift-{alert['alert_id']}"):
                    note = st.text_input("Resolution (owner only)", key=f"drift-note-{alert['alert_id']}")
                    if st.form_submit_button("Resolve alert"):
                        reply = api.resolve_drift(alert["alert_id"], note)
                        if reply.ok:
                            st.success("Drift alert resolved.")
                        else:
                            show_error(reply)
    elif not drift_reply.ok:
        show_error(drift_reply)
    reply = api.batches(source)
    if reply.ok:
        st.dataframe(pd.DataFrame(reply.data), hide_index=True, width="stretch")
    else:
        show_error(reply)


with tab_reconcile:
    run_help = "Compare consumer views and explain BI tool gaps on published gold."
    if st.button("Run reconciliation", help=run_help):
        reply = api.reconcile()
        if not reply.ok:
            show_error(reply)
    latest = api.reconciliation()
    if not latest.ok:
        st.info("No reconciliation run yet." if latest.status == 404 else latest.error)
    else:
        run_data = latest.data
        colour = {"green": "🟢", "red": "🔴", "no_data": "⚪"}[run_data["status"]]
        st.subheader(f"{colour} Run {run_data['run_id']}: {run_data['status']}")
        consumers = pd.DataFrame(run_data["consumers"])
        if not consumers.empty:
            otif = consumers[consumers["metric"] == "otif"]
            grid = otif.pivot_table(index="period", columns="grain_key", values="status", aggfunc="first")
            st.caption("Two current OTIF consumer views, rolled up to month × plant (green = identical):")
            st.dataframe(grid.replace({"green": "🟢", "red": "🔴"}), width="stretch")
            st.caption("Legacy view gap (v2 count basis − governed v3), by period:")
            legacy_gap = otif.pivot_table(index="period", columns="grain_key", values="legacy_delta")
            st.dataframe(legacy_gap.round(4), width="stretch")
        for tool, s in run_data["summary"].get("tools", {}).items():
            with st.container(border=True):
                st.markdown(f"**{tool}**: identified causes {', '.join(s['identified_causes']) or 'none'}; "
                            f"undetermined {', '.join(s['undetermined_causes']) or 'none'}; "
                            f"dominant **{s['dominant_cause']}**; "
                            f"export reproduced {s['export_match_share']:.1%}")
                rows = pd.DataFrame([t for t in run_data["tools"] if t["tool"] == tool])
                if not rows.empty:
                    detail = rows[["period", "governed", "measured", "gap", "gap_flagged"]].copy()
                    detail = pd.concat([detail, pd.json_normalize(list(rows["contributions"]))], axis=1)
                    st.dataframe(detail.round(5), hide_index=True, width="stretch")
            st.caption("Contributions are single-switch deltas from governed; they interact and do not "
                       "sum to the gap.")


with tab_knowledge:
    st.caption("Ask the knowledge base (SOPs, source notes, memos, specifications, prior resolutions). The "
               "asker's access level is applied in SQL before ranking, so restricted material never reaches "
               "the model; an answer not supported by the excerpts is refused.")
    left, middle, right = st.columns([2, 1, 1])
    question = left.text_input("Question", placeholder="How long do I have to resolve a blocking exception?")
    role = middle.selectbox("Ask as", ["analyst", "plant_user", "leadership", "data_owner"],
                            help="Only an owner key may ask as leadership or data_owner.")
    plant = right.selectbox("Plant", ["PLT-01", "PLT-02", "PLT-03"]) if role == "plant_user" else None
    if st.button("Ask", type="primary", disabled=not question):
        reply = api.ask(question, role, plant)
        if not reply.ok:
            show_error(reply)
        else:
            data = reply.data
            if data["refused"]:
                st.warning(f"Refused ({data['outcome']}): {data['answer']}")
            else:
                st.success(data["answer"])
            if data["cited_documents"]:
                st.caption("Cited: " + ", ".join(data["cited_documents"]))
            excerpts = pd.DataFrame(data["excerpts"])
            if not excerpts.empty:
                excerpts["notes"] = excerpts["notes"].map(lambda notes: " ".join(notes))
                st.caption("Excerpts the model was given (the access filter chose these):")
                st.dataframe(excerpts, hide_index=True, width="stretch")
            elif data["outcome"] == "refused_access":
                st.caption("No excerpt was retrieved or shown: the nearest match is above this access level.")
    with st.expander("What is in the knowledge base"):
        docs = api.kb_documents()
        if not docs.ok:
            show_error(docs)
        else:
            st.caption(f"{docs.data['prior_resolutions']} prior exception resolutions, plus these documents:")
            st.dataframe(pd.DataFrame(docs.data["documents"]), hide_index=True, width="stretch")


with tab_ops:
    days = st.selectbox("Window", [1, 7, 30], index=1, format_func=lambda d: f"last {d} day(s)")
    ops = api.ops_summary(days)
    if not ops.ok:
        show_error(ops)
    else:
        data = ops.data
        if data["alerts"]:
            for alert in data["alerts"]:
                st.warning(alert)
        else:
            st.success("No operator alerts.")
        st.subheader("Pipeline")
        st.dataframe(pd.DataFrame(data["pipeline"]), hide_index=True, width="stretch")
        st.subheader("Open exceptions")
        st.dataframe(pd.DataFrame(data["exception_backlog"]), hide_index=True, width="stretch")
        st.subheader("Model usage (LLM and embeddings)")
        usage = pd.DataFrame(data["model_usage"])
        if not usage.empty:
            left, mid, right = st.columns(3)
            left.metric("Calls", int(usage["calls"].sum()))
            mid.metric("Errors", int(usage["errors"].sum()))
            right.metric("Cost (USD)", f"{float(usage['cost_usd'].astype(float).sum()):.4f}")
        st.dataframe(usage, hide_index=True, width="stretch")
        st.subheader("Exception agent")
        st.dataframe(pd.DataFrame(data["agent_runs"]), hide_index=True, width="stretch")
        st.dataframe(pd.DataFrame(data["tool_usage"]), hide_index=True, width="stretch")


def _label(items: list[dict], exception_id: int) -> str:
    e = next(x for x in items if x["exception_id"] == exception_id)
    return f"#{exception_id} {e['rule_id']} {e['row_key']}"


with tab_exceptions:
    summary_reply = api.exception_summary(source)
    if summary_reply.ok:
        summary = summary_reply.data
        cols = st.columns(4)
        cols[0].metric("Open blocking", summary["open_blocking"])
        cols[1].metric("Open", summary["by_status"].get("open", 0))
        cols[2].metric("Assigned", summary["by_status"].get("assigned", 0))
        cols[3].metric("Resolved", summary["by_status"].get("resolved", 0))
        if summary["open_by_owner"]:
            st.caption("Open by owner: " + ", ".join(f"{k} {v}" for k, v in summary["open_by_owner"].items()))
    else:
        show_error(summary_reply)

    f1, f2, f3, f4 = st.columns(4)
    status_filter = f1.selectbox("Status", ["", "open", "assigned", "resolved"], index=1)
    severity_filter = f2.selectbox("Severity", ["", "block", "warn"])
    rule_filter = f3.selectbox("Rule", ["", *[f"V{n:03d}" for n in range(1, 12)]])
    owner_filter = f4.text_input("Owner", placeholder="e.g. master_data:customers")
    page = api.exceptions(source=source, status=status_filter, severity=severity_filter, rule_id=rule_filter,
                          owner=owner_filter, limit=200)
    if not page.ok:
        show_error(page)
    elif not page.data["items"]:
        st.info("No exceptions match.")
    else:
        items = page.data["items"]
        st.caption(f"{page.data['total']} matching; showing {len(items)}.")
        st.dataframe(pd.DataFrame([{
            "id": e["exception_id"], "rule": e["rule_id"], "severity": e["severity"], "status": e["status"],
            "row": e["row_key"], "file": e["file_name"], "owner": e["owner"], "reason": e["reason"],
        } for e in items]), hide_index=True, width="stretch")
        chosen_id = st.selectbox("Work an exception", [e["exception_id"] for e in items],
                                 format_func=lambda i: _label(items, i))
        item = api.exception(chosen_id)
        if not item.ok:
            show_error(item)
        else:
            e = item.data
            st.markdown(f"**{e['rule_id']} ({e['severity']})**: {e['reason']}")
            st.markdown(f"Suggested fix: {e['suggested_fix'] or '-'}")
            left, right = st.columns(2)
            left.caption("Evidence")
            left.json(e["details"])
            right.caption(f"Raw record as received ({e['file_name']}, row {e['source_row']})")
            right.json(e["raw_record"])
            if e["events"]:
                st.caption("History: " + "; ".join(f"{v['action']} by {v['actor']}" for v in e["events"]))
            if e["status"] == "resolved":
                st.info(f"Resolved ({e['resolution_kind']}) by {e['resolved_by']}: {e['resolution']}")
            else:
                with st.form(f"assign-{chosen_id}"):
                    new_owner = st.text_input("Assign to", value=e["owner"] or "")
                    if st.form_submit_button("Assign"):
                        reply = api.assign(chosen_id, new_owner)
                        if reply.ok:
                            st.success(f"Assigned to {new_owner}.")
                        else:
                            show_error(reply)
                with st.form(f"resolve-{chosen_id}"):
                    kind = st.selectbox("Resolution", ["accept", "exclude", "fixed_at_source"],
                                        help="accept: publish the row as sent; exclude: never publish it; "
                                             "fixed_at_source: a corrected extract will replace it")
                    note = st.text_area("What was decided and why (owner only)")
                    if st.form_submit_button("Resolve"):
                        reply = api.resolve(chosen_id, kind, note)
                        if reply.ok:
                            st.success("Resolved.")
                        else:
                            show_error(reply)
            if st.button("Re-run validation for this batch", key=f"revalidate-{e['batch_id']}"):
                reply = api.revalidate(e["batch_id"])
                if reply.ok:
                    st.success(f"Re-validated: {reply.data['open_exceptions']} open, "
                               f"{reply.data['auto_resolved']} closed as no longer violated.")
                else:
                    show_error(reply)
