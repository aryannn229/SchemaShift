import threading
import time
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2 as httpx
import pytest

from schemashift.ai import (
    AdvisorError,
    AnthropicAdvisor,
    InMemoryCache,
    MockAdvisor,
    agreement_rate,
    attach_opinions,
    make_advisor,
)
from schemashift.ai.cache import cache_key
from schemashift.ai.prompts import (
    PROMPT_VERSION,
    build_prompt,
    parse_suggestion,
    render_table,
    schema_excerpt,
)
from schemashift.models import AISuggestion
from schemashift.optimizer import extract_features
from schemashift.pipeline import compile_sql

SQL = """
CREATE TABLE customers (id INT PRIMARY KEY, name TEXT NOT NULL, secret_note TEXT);
CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE, total NUMERIC(8,2));
CREATE TABLE emp (id INT PRIMARY KEY, boss INT REFERENCES emp(id));
INSERT INTO customers (id, name) VALUES (1, 'TOPSECRET-ROW-VALUE');
"""
GOOD = '{"decision": "EMBED", "confidence": 0.8, "justification": "small and read together"}'


def setup():  # type: ignore[no-untyped-def]
    result = compile_sql(SQL.split("INSERT")[0])
    feats = extract_features(result.graph, result.ir)
    return result, feats


class FakeClient:
    """Minimal stand-in for ``anthropic.Anthropic`` returning scripted replies."""

    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        stop = "refusal" if reply == "REFUSE" else "end_turn"
        content = (
            []
            if reply == "REFUSE"
            else [
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text=reply),
            ]
        )
        return SimpleNamespace(content=content, stop_reason=stop)


# ----------------------------------------------------------------------- prompts
def test_prompt_contains_structure_and_features_but_no_data() -> None:
    result, feats = setup()
    rel = feats["fk:orders.customer_id->customers.id"]
    prompt = build_prompt(rel, schema_excerpt(result.schema_, rel))
    assert 'CREATE TABLE "customers"' in prompt and 'CREATE TABLE "orders"' in prompt
    assert "FOREIGN KEY" in prompt and "estimated children per parent: 20" in prompt
    assert "strict JSON" in prompt and "EMBED" in prompt
    assert "TOPSECRET" not in prompt  # seed data is never sent


def test_seed_values_never_reach_the_advisor() -> None:
    seed = "INSERT" + SQL.split("INSERT")[1]
    full = compile_sql(SQL.split("INSERT")[0], "", seed)
    feats = extract_features(full.graph, full.ir, (), full.seed_queries)
    rel = feats["fk:orders.customer_id->customers.id"]
    client = FakeClient([GOOD])
    AnthropicAdvisor(client=client).suggest_placement(rel, schema_excerpt(full.schema_, rel))
    sent = str(client.calls[0])
    assert "TOPSECRET" not in sent and "customers" in sent


def test_render_table() -> None:
    result, _ = setup()
    text = render_table(result.schema_.tables["orders"])
    assert 'PRIMARY KEY ("id")' in text and "ON DELETE CASCADE" in text and "NUMERIC(8,2)" in text


@pytest.mark.parametrize(
    "text",
    [
        GOOD,
        f"Sure! {GOOD} hope that helps",
        f"```json\n{GOOD}\n```",
        GOOD.replace("EMBED", "embed"),
    ],
)
def test_parse_accepts_wrapped_json(text: str) -> None:
    s = parse_suggestion(text)
    assert s.decision == "EMBED" and s.confidence == 0.8


@pytest.mark.parametrize(
    "text",
    [
        "",
        "no json here",
        "{broken",
        '{"decision": "MAYBE", "confidence": 0.5, "justification": "x"}',
        '{"decision": "EMBED", "confidence": 1.5, "justification": "x"}',
        '{"decision": "EMBED"}',
        "[1, 2]",
    ],
)
def test_parse_rejects_invalid(text: str) -> None:
    with pytest.raises(AdvisorError):
        parse_suggestion(text)


# ---------------------------------------------------------------- Anthropic advisor
def test_anthropic_advisor_calls_the_sdk_correctly() -> None:
    _, feats = setup()
    client = FakeClient([GOOD])
    advisor = AnthropicAdvisor(model="claude-opus-5-5", client=client)
    out = advisor.suggest_placement(feats["fk:orders.customer_id->customers.id"], "excerpt")
    assert out == AISuggestion(
        decision="EMBED", confidence=0.8, justification="small and read together"
    )
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["output_config"] == {"effort": "low"}
    assert "system" in call and call["messages"][0]["role"] == "user"
    assert "thinking" not in call and "temperature" not in call  # rejected by this model family


def test_invalid_json_is_retried_once() -> None:
    _, feats = setup()
    client = FakeClient(["not json", GOOD])
    out = AnthropicAdvisor(client=client).suggest_placement(
        feats["fk:orders.customer_id->customers.id"], "x"
    )
    assert out.decision == "EMBED" and len(client.calls) == 2
    assert "ONLY the JSON" in client.calls[1]["messages"][0]["content"]


def test_two_invalid_replies_give_up() -> None:
    _, feats = setup()
    client = FakeClient(["nope", "still nope"])
    with pytest.raises(AdvisorError, match="after a retry"):
        AnthropicAdvisor(client=client).suggest_placement(
            feats["fk:orders.customer_id->customers.id"], "x"
        )
    assert len(client.calls) == 2


def request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (anthropic.APITimeoutError(request=request()), "timed out"),
        (anthropic.APIConnectionError(request=request()), "failed"),
        ("REFUSE", "declined"),
    ],
)
def test_api_failures_become_advisor_errors(error: Any, message: str) -> None:
    _, feats = setup()
    with pytest.raises(AdvisorError, match=message):
        AnthropicAdvisor(client=FakeClient([error])).suggest_placement(
            feats["fk:orders.customer_id->customers.id"], "x"
        )


def test_default_client_uses_a_15_second_timeout() -> None:
    advisor = AnthropicAdvisor(api_key="sk-test")
    assert advisor._client.timeout == 15.0 and advisor.model == "claude-opus-5-5"


# ----------------------------------------------------------------------- mock
def test_mock_is_deterministic_and_marked_unavailable() -> None:
    _, feats = setup()
    mock = MockAdvisor()
    rel = feats["fk:orders.customer_id->customers.id"]
    assert mock.suggest_placement(rel, "") == mock.suggest_placement(rel, "")
    assert mock.available is False and mock.name == "mock"
    big = rel.model_copy(update={"estimated_child_count_per_parent": 500})
    assert mock.suggest_placement(big, "").decision == "REFERENCE"
    one = rel.model_copy(update={"cardinality": "1:1"})
    assert mock.suggest_placement(one, "").decision == "EMBED"


def test_make_advisor_without_key_is_the_mock() -> None:
    assert isinstance(make_advisor(""), MockAdvisor)
    assert isinstance(make_advisor("sk-test", "claude-opus-5-5"), AnthropicAdvisor)


# ------------------------------------------------------------------ attach_opinions
class Scripted:
    name = "anthropic"
    available = True
    model = "m"

    def __init__(self, decision: str = "EMBED", delay: float = 0.0, fail: bool = False) -> None:
        self.decision, self.delay, self.fail = decision, delay, fail
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def suggest_placement(self, rel: Any, schema_excerpt: str) -> AISuggestion:
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            if self.fail:
                raise AdvisorError("boom")
            return AISuggestion(decision=self.decision, confidence=0.7, justification="j")  # type: ignore[arg-type]
        finally:
            with self.lock:
                self.active -= 1


def test_opinions_are_attached_without_changing_the_decision() -> None:
    result, feats = setup()
    before = {k: d.decision for k, d in result.plan.decisions.items()}
    plan = attach_opinions(result.plan, feats, result.schema_, Scripted("EMBED"))
    assert {k: d.decision for k, d in plan.decisions.items()} == before  # optimizer is final
    d = plan.decisions["fk:orders.customer_id->customers.id"]
    assert d.ai is not None and d.ai.available and d.ai.source == "anthropic"
    assert d.agree is (d.decision == "EMBED")
    assert plan.decisions["fk:emp.boss->emp.id"].ai is None  # self-references are never asked


def test_disagreement_is_recorded() -> None:
    result, feats = setup()
    optimizer = result.plan.decisions["fk:orders.customer_id->customers.id"].decision
    opposite = "REFERENCE" if optimizer == "EMBED" else "EMBED"
    plan = attach_opinions(result.plan, feats, result.schema_, Scripted(opposite))
    d = plan.decisions["fk:orders.customer_id->customers.id"]
    assert d.agree is False and d.decision == optimizer and d.ai.suggestion.decision == opposite  # type: ignore[union-attr]
    assert agreement_rate(plan) == 0.0


def test_failures_do_not_break_the_compile() -> None:
    result, feats = setup()
    plan = attach_opinions(result.plan, feats, result.schema_, Scripted(fail=True))
    d = plan.decisions["fk:orders.customer_id->customers.id"]
    assert (
        d.ai is not None
        and d.ai.source == "unavailable"
        and not d.ai.available
        and "boom" in (d.ai.error or "")
    )
    assert d.agree is None and agreement_rate(plan) is None


def test_unexpected_advisor_exceptions_are_contained() -> None:
    class Broken(Scripted):
        def suggest_placement(self, rel: Any, schema_excerpt: str) -> AISuggestion:
            raise RuntimeError("bug")

    result, feats = setup()
    plan = attach_opinions(result.plan, feats, result.schema_, Broken())
    assert "RuntimeError" in (plan.decisions["fk:orders.customer_id->customers.id"].ai.error or "")  # type: ignore[union-attr]


def test_mock_opinions_are_not_counted_as_agreement() -> None:
    result, feats = setup()
    plan = attach_opinions(result.plan, feats, result.schema_, MockAdvisor())
    d = plan.decisions["fk:orders.customer_id->customers.id"]
    assert d.ai is not None and d.ai.source == "mock" and not d.ai.available and d.agree is None
    assert agreement_rate(plan) is None


def test_concurrency_is_bounded_to_five() -> None:
    tables = "".join(
        f"CREATE TABLE c{i} (id INT PRIMARY KEY, p INT REFERENCES p(id));" for i in range(12)
    )
    result = compile_sql("CREATE TABLE p (id INT PRIMARY KEY);" + tables)
    feats = extract_features(result.graph, result.ir)
    advisor = Scripted(delay=0.05)
    attach_opinions(result.plan, feats, result.schema_, advisor, max_workers=50)
    assert advisor.calls == 12 and 1 < advisor.max_active <= 5


def test_cache_avoids_repeat_calls() -> None:
    result, feats = setup()
    cache = InMemoryCache()
    advisor = Scripted()
    attach_opinions(result.plan, feats, result.schema_, advisor, cache)
    first = advisor.calls
    again = attach_opinions(result.plan, feats, result.schema_, advisor, cache)
    assert advisor.calls == first and len(cache) == first
    assert again.decisions["fk:orders.customer_id->customers.id"].ai is not None


def test_failed_calls_are_not_cached() -> None:
    result, feats = setup()
    cache = InMemoryCache()
    attach_opinions(result.plan, feats, result.schema_, Scripted(fail=True), cache)
    assert len(cache) == 0


def test_cache_key_depends_on_version_model_and_prompt() -> None:
    base = cache_key("m", "p")
    assert (
        base != cache_key("m2", "p")
        and base != cache_key("m", "p2")
        and base != cache_key("m", "p", "other")
    )
    assert cache_key("m", "p") == cache_key("m", "p", PROMPT_VERSION)


def test_compile_works_with_the_mock_and_without_any_advisor() -> None:
    without = compile_sql(SQL.split("INSERT")[0])
    with_mock = compile_sql(SQL.split("INSERT")[0], advisor=MockAdvisor())
    assert all(d.ai is None for d in without.plan.decisions.values())
    assert with_mock.plan.decisions["fk:orders.customer_id->customers.id"].ai.source == "mock"  # type: ignore[union-attr]
    assert {k: d.decision for k, d in without.plan.decisions.items()} == {
        k: d.decision for k, d in with_mock.plan.decisions.items()
    }
    assert without.equivalence.final.counts == with_mock.equivalence.final.counts


@pytest.mark.live
def test_live_advisor_returns_a_valid_suggestion() -> None:
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    result, feats = setup()
    rel = feats["fk:orders.customer_id->customers.id"]
    out = AnthropicAdvisor().suggest_placement(rel, schema_excerpt(result.schema_, rel))
    assert out.decision in ("EMBED", "REFERENCE") and 0 <= out.confidence <= 1 and out.justification
