from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import REPO_ROOT, Settings, get_settings
from app.main import app
from app.routers.mappings import get_llm_client
from db.migrate import migrate
from llm.client import (
    Completion,
    DbRecorder,
    GroqBackend,
    InvalidOutput,
    LLMClient,
    LLMError,
    MemoryRecorder,
    build_client,
)
from pipeline.mapper import llm as llm_mapper
from pipeline.mapper.heuristic import propose as heuristic_propose
from pipeline.profile import SourceProfile

DATA = REPO_ROOT / "data"


@dataclass
class ScriptedBackend:
    """Returns queued answers in order; an Exception in the queue is raised instead."""

    answers: list[str | Exception]
    provider: str = "fake"
    model: str = "fake-model"
    prompts: list[str] = field(default_factory=list)

    def complete(self, system: str, user: str, schema: dict, max_tokens: int) -> Completion:
        self.prompts.append(user)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Completion(answer, input_tokens=1200, output_tokens=300, stop_reason="end_turn")


def _client(*answers: str | Exception) -> tuple[LLMClient, ScriptedBackend, MemoryRecorder]:
    backend, recorder = ScriptedBackend(list(answers)), MemoryRecorder()
    return LLMClient(backend, recorder, price_in_per_mtok=2.0, price_out_per_mtok=10.0), backend, recorder


@pytest.fixture(scope="module")
def plt01(generated_profiles: dict[str, SourceProfile]) -> tuple[SourceProfile, list[str]]:
    profile = generated_profiles["plt01"]
    return profile, profile.header_variants[0].columns


def _answer_from(proposal_columns: list[dict]) -> str:
    return json.dumps({"columns": proposal_columns})


def _heuristic_answer(profile: SourceProfile, header: list[str]) -> list[dict]:
    return [c.model_dump() for c in heuristic_propose(profile, header).columns]


# --- client ----------------------------------------------------------------------


def test_client_records_ok_call_with_tokens_cost_and_prompt_hash() -> None:
    client, _, recorder = _client('{"x": 1}')
    assert client.complete_json("p", "sys", "user", {}, parse=lambda d: d["x"]) == 1
    (call,) = recorder.calls
    assert (call.outcome, call.input_tokens, call.output_tokens) == ("ok", 1200, 300)
    assert call.cost_usd == pytest.approx(1200 * 2 / 1e6 + 300 * 10 / 1e6)
    assert len(call.prompt_hash) == 64 and call.model == "fake-model"


def test_client_records_invalid_json_and_provider_errors() -> None:
    client, _, recorder = _client("not json", LLMError("400: credit balance too low"))
    with pytest.raises(InvalidOutput):
        client.complete_json("p", "sys", "user", {}, parse=lambda d: d)
    with pytest.raises(LLMError):
        client.complete_json("p", "sys", "user", {}, parse=lambda d: d)
    assert [c.outcome for c in recorder.calls] == ["invalid_output", "error"]
    assert recorder.calls[1].input_tokens is None and "credit" in (recorder.calls[1].error or "")


def test_provider_none_builds_no_client() -> None:
    assert build_client(Settings(llm_provider="none"), MemoryRecorder()) is None
    with pytest.raises(ValueError, match="unsupported"):
        build_client(Settings(llm_provider="openrouter"), MemoryRecorder())


def _groq(handler: object) -> GroqBackend:
    return GroqBackend("openai/gpt-oss-120b", "gsk_test",
                       http=httpx.Client(transport=httpx.MockTransport(handler)))  # type: ignore[arg-type]


def test_groq_backend_sends_strict_schema_and_reads_usage() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"x": 1}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 120}})

    completion = _groq(handler).complete("sys", "user", {"type": "object"}, 4000)
    assert (completion.text, completion.input_tokens, completion.output_tokens) == ('{"x": 1}', 900, 120)
    assert seen["auth"] == "Bearer gsk_test"
    fmt = seen["body"]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


@pytest.mark.parametrize(
    ("response", "message"),
    [(httpx.Response(429, json={"error": {"message": "rate limit reached"}}), "429: rate limit reached"),
     (httpx.Response(200, json={"choices": [{"message": {"content": "{"}, "finish_reason": "length"}],
                                "usage": {}}), "finish_reason=length")],
)
def test_groq_backend_errors_become_llm_errors(response: httpx.Response, message: str) -> None:
    with pytest.raises(LLMError, match=message):
        _groq(lambda request: response).complete("s", "u", {}, 10)


def test_groq_backend_waits_once_on_a_short_rate_limit() -> None:
    responses = [httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "TPM"}}),
                 httpx.Response(200, json={"choices": [{"message": {"content": "{}"},
                                                         "finish_reason": "stop"}]})]
    slept: list[float] = []
    http = httpx.Client(transport=httpx.MockTransport(lambda r: responses.pop(0)))
    backend = GroqBackend("m", "k", http=http, sleep=slept.append)
    assert backend.complete("s", "u", {}, 10).text == "{}"
    assert slept == [3.5]


def test_groq_backend_waits_out_repeated_minute_limits_within_a_budget() -> None:
    tpm = httpx.Response(429, headers={"retry-after": "40"}, json={"error": {"message": "TPM"}})
    ok = httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})
    responses = [tpm, tpm, tpm, ok]
    slept: list[float] = []
    http = httpx.Client(transport=httpx.MockTransport(lambda r: responses.pop(0)))
    assert GroqBackend("m", "k", http=http, sleep=slept.append).complete("s", "u", {}, 10).text == "{}"
    assert slept == [40.5, 40.5, 40.5]

    slept.clear()
    endless = GroqBackend("m", "k", http=httpx.Client(transport=httpx.MockTransport(lambda r: tpm)),
                          sleep=slept.append)
    with pytest.raises(LLMError, match="429: TPM"):
        endless.complete("s", "u", {}, 10)
    assert sum(slept) <= 240 and len(slept) == 5


def test_groq_backend_does_not_wait_on_a_long_rate_limit() -> None:
    long_wait = httpx.Response(429, headers={"retry-after": "600"},
                               json={"error": {"message": "daily limit"}})
    slept: list[float] = []
    backend = GroqBackend("m", "k", http=httpx.Client(transport=httpx.MockTransport(lambda r: long_wait)),
                          sleep=slept.append)
    with pytest.raises(LLMError, match="429: daily limit"):
        backend.complete("s", "u", {}, 10)
    assert slept == []


def test_groq_backend_requires_a_key() -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        GroqBackend("openai/gpt-oss-120b", "")


# --- mapper ----------------------------------------------------------------------


def test_valid_llm_answer_becomes_llm_proposal(plt01: tuple[SourceProfile, list[str]]) -> None:
    profile, header = plt01
    columns = _heuristic_answer(profile, header)
    columns[-1] = {**columns[-1], "canonical_col": None, "transforms": [], "rationale": "unsure"}
    client, _, recorder = _client(_answer_from(columns))
    result = llm_mapper.propose(profile, header, client)
    assert result.used_llm and not result.needs_review
    assert result.proposal.proposed_by == "llm"
    assert result.proposal.columns[-1].canonical_col is None
    assert result.heuristic.proposed_by == "heuristic"
    assert [c.outcome for c in recorder.calls] == ["ok"]


def test_invalid_answer_is_retried_once_with_the_validation_error(
        plt01: tuple[SourceProfile, list[str]]) -> None:
    profile, header = plt01
    columns = _heuristic_answer(profile, header)
    duplicated = [*columns[:-1], {**columns[-1], "canonical_col": columns[0]["canonical_col"]}]
    client, backend, recorder = _client(_answer_from(duplicated), _answer_from(columns))
    result = llm_mapper.propose(profile, header, client)
    assert result.used_llm
    assert [c.outcome for c in recorder.calls] == ["invalid_output", "ok"]
    assert "rejected by validation" in backend.prompts[1] and "more than once" in backend.prompts[1]


def test_hallucinated_column_twice_falls_back_to_heuristic_flagged(
        plt01: tuple[SourceProfile, list[str]]) -> None:
    """Plan §2.6: LLM returns a hallucinated column → contract rejects, heuristic used, flagged for review."""
    profile, header = plt01
    columns = _heuristic_answer(profile, header)
    invented = _answer_from([*columns, {**columns[0], "source_col": "ship_to_city"}])
    client, _, recorder = _client(invented, "{broken")
    result = llm_mapper.propose(profile, header, client)
    assert not result.used_llm and result.needs_review
    assert result.proposal.proposed_by == "heuristic"
    assert [c.outcome for c in recorder.calls] == ["invalid_output", "invalid_output"]  # never a third call
    assert result.notes[-1] == "fell back to the heuristic proposal; needs review"


def test_provider_error_falls_back_without_retry(plt01: tuple[SourceProfile, list[str]]) -> None:
    profile, header = plt01
    client, backend, recorder = _client(LLMError("503: overloaded"), "unused")
    result = llm_mapper.propose(profile, header, client)
    assert result.needs_review and not result.used_llm
    assert len(backend.prompts) == 1 and [c.outcome for c in recorder.calls] == ["error"]


def test_prompt_carries_profile_statistics_not_raw_rows(plt01: tuple[SourceProfile, list[str]]) -> None:
    profile, header = plt01
    heuristic = heuristic_propose(profile, header)
    payload = json.loads(llm_mapper.build_prompt(profile, header, heuristic, []))
    assert payload["source"]["header"] == header
    assert {c["name"] for c in payload["canonical_schema"]} >= {"order_no", "invoiced_weight_lb"}
    for column in payload["source"]["columns"]:
        assert len(column["shapes"]) <= llm_mapper.MAX_EXAMPLES_PER_COLUMN
    assert "rows" not in json.dumps(payload["source"])
    schema = llm_mapper.answer_schema(header)
    assert schema["properties"]["columns"]["items"]["properties"]["source_col"]["enum"] == header


# --- database + API --------------------------------------------------------------


@pytest.mark.postgres
def test_db_recorder_writes_ops_llm_calls(pg_url: str) -> None:
    migrate(pg_url)
    engine = create_engine(pg_url)
    client = LLMClient(ScriptedBackend(['{"x": 1}', "nope"]), DbRecorder(engine), 2.0, 10.0)
    client.complete_json("mapping_proposal", "s", "u", {}, parse=lambda d: d)
    with pytest.raises(InvalidOutput):
        client.complete_json("mapping_proposal", "s", "u", {}, parse=lambda d: d)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT outcome, provider, cost_usd FROM ops.llm_calls ORDER BY llm_call_id")).all()
    engine.dispose()
    assert [(r.outcome, r.provider) for r in rows] == [("ok", "fake"), ("invalid_output", "fake")]


@pytest.fixture
def api(pg_url: str, generated_profiles: dict[str, SourceProfile]) -> Iterator[tuple[TestClient, list]]:
    migrate(pg_url)
    settings = Settings(database_url=pg_url, llm_provider="none", data_dir=DATA,
                        api_keys={"k-analyst": "analyst", "k-owner": "owner"})
    queue: list = []
    app.dependency_overrides[get_settings] = lambda: settings
    fake = LLMClient(ScriptedBackend(queue), MemoryRecorder(), 2, 10)
    app.dependency_overrides[get_llm_client] = lambda: fake
    try:
        yield TestClient(app), queue
    finally:
        app.dependency_overrides.clear()


@pytest.mark.postgres
def test_propose_stores_heuristic_and_llm_versions_and_reports_fallback(
        api: tuple[TestClient, list], generated_profiles: dict[str, SourceProfile]) -> None:
    client, queue = api
    profile = generated_profiles["plt01"]
    first, second = (v.columns for v in profile.header_variants)
    llm_first = _heuristic_answer(profile, first)
    llm_first[-1] = {**llm_first[-1], "canonical_col": None, "transforms": []}
    queue += [_answer_from(llm_first), LLMError("400: credit balance too low")]

    body = client.post("/sources/plt01/mappings/propose", headers={"X-API-Key": "k-analyst"}).json()
    assert [v["proposed_by"] for v in body["versions"]] == ["heuristic", "llm", "heuristic"]
    assert [(s["used_llm"], s["needs_review"]) for s in body["llm"]] == [(True, False), (False, True)]
    assert "credit balance" in body["llm"][1]["notes"][0]
    assert second == body["versions"][2]["header"]


@pytest.mark.skipif(os.environ.get("RUN_LIVE_LLM") != "1",
                    reason="set RUN_LIVE_LLM=1 to call the real provider")
def test_live_provider_returns_a_contract_valid_mapping(plt01: tuple[SourceProfile, list[str]]) -> None:
    profile, header = plt01
    recorder = MemoryRecorder()
    client = build_client(get_settings(), recorder)
    assert client is not None, "LLM_PROVIDER is none"
    result = llm_mapper.propose(profile, header, client)
    assert result.used_llm, result.notes
