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
tab_sources, tab_findings, tab_mappings, tab_batches = st.tabs(["Sources & upload", "Findings", "Mappings",
                                                                "Batches"])

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
    reply = api.batches(source)
    if reply.ok:
        st.dataframe(pd.DataFrame(reply.data), hide_index=True, width="stretch")
    else:
        show_error(reply)
