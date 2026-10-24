import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from lfx.components.enterprise_delivery.candidate_contracts import (
    TicketCandidate,
    follow_up_questions,
    missing_information,
    source_text,
    validate_candidate,
)
from lfx.components.enterprise_delivery.candidate_model import extract_candidate
from lfx.components.enterprise_delivery.candidate_store import CandidateStore, drafts, prepare_flow_input
from lfx.components.enterprise_delivery.contracts import ConnectionProfile, DeliveryError
from lfx.components.enterprise_delivery.deepseek_model import DeepSeekModel
from lfx.components.enterprise_delivery.journal import Journal, metadata
from pydantic import ValidationError
from sqlalchemy import create_engine, select

SOURCE = "设备 A17 反复报 E12，重启两次未恢复，影响前台3人无法收款。"


@pytest.fixture
def candidate():
    return {
        "summary": "A17 报 E12 且无法收款",
        "detail": SOURCE,
        "facts": {
            "asset_ref": "A17",
            "symptom": "反复报 E12",
            "attempted_action": "重启两次未恢复",
            "business_impact": "影响前台3人无法收款",
            "workaround": None,
        },
        "evidence": [
            {"field": "asset_ref", "quote": "设备 A17"},
            {"field": "symptom", "quote": "反复报 E12"},
            {"field": "attempted_action", "quote": "重启两次未恢复"},
            {"field": "business_impact", "quote": "影响前台3人无法收款"},
        ],
    }


@pytest.fixture
def profile():
    return ConnectionProfile(
        tenant="synthetic-operations",
        audience="servicenow:synthetic",
        base_url="https://synthetic.service-now.com",
        token_env="SYNTHETIC_TOKEN",
        allowed_user_ids=["executor", "another"],
        review_public_key="synthetic-test-public-key",
    )


@pytest.fixture
def store(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL", "sqlite:///" + str(tmp_path / "intake.sqlite"))
    engine = create_engine(url)
    metadata.drop_all(engine)
    metadata.create_all(engine)
    yield CandidateStore(engine)
    metadata.drop_all(engine)
    engine.dispose()


def create(store, profile, candidate):
    return store.create(
        {"source": SOURCE, "candidate": candidate, "model_run": {"mode": "test-fixture"}},
        "executor",
        "demo",
        profile,
        "SYN-INTAKE",
        "SYN-CUSTOMER",
    )


def confirm(store, profile, draft, candidate):
    return store.confirm(
        draft["id"], draft["revision"], {"candidate": candidate, "urgency": 2}, "executor", "demo", profile
    )


def test_source_quote_and_missing_information(candidate):
    valid = validate_candidate(SOURCE, candidate)
    assert missing_information(valid) == []
    assert len(follow_up_questions(TicketCandidate())) == 3


@pytest.mark.parametrize("extra", [{"tenant": "another"}, {"profile": "admin"}, {"approved": True}])
def test_model_cannot_supply_controlled_fields(candidate, extra):
    with pytest.raises(ValidationError):
        validate_candidate(SOURCE, {**candidate, **extra})


def test_fabricated_quote_and_unsupported_fact_are_rejected(candidate):
    candidate["evidence"][0]["quote"] = "不存在的设备 B99"
    with pytest.raises(DeliveryError):
        validate_candidate(SOURCE, candidate)
    candidate["evidence"] = []
    with pytest.raises(DeliveryError):
        validate_candidate(SOURCE, candidate)


def test_source_bounds_and_contact_cleanup():
    assert "@" not in source_text("请联系 synthetic@example.invalid")
    for value in ["", " " * 3, "x" * 12001]:
        with pytest.raises(DeliveryError):
            source_text(value)


def test_unknown_impact_is_not_complete_even_when_model_fills_a_string(candidate):
    raw = SOURCE + "影响范围尚未确认"
    candidate["facts"]["business_impact"] = "影响范围尚未确认"
    candidate["evidence"][-1] = {"field": "business_impact", "quote": "影响范围尚未确认"}
    result = validate_candidate(raw, candidate)
    assert result.facts.business_impact is None
    assert missing_information(result) == ["business_impact"]


def test_unconfirmed_and_forged_reference_block_preparation(store, profile, candidate):
    draft = create(store, profile, candidate)
    with pytest.raises(DeliveryError):
        prepare_flow_input(Journal(store.engine), {"confirmed": True}, "executor", "demo", profile)
    with pytest.raises(DeliveryError):
        store.prepare("forged-reference", "executor", "demo", profile)
    assert draft["state"] == "draft"


def test_confirmation_and_prepare_freeze_content(store, profile, candidate):
    draft = confirm(store, profile, create(store, profile, candidate), candidate)
    record = prepare_flow_input(
        Journal(store.engine),
        {"confirmation_id": draft["confirmation_id"], "ticket": {"summary": "injected client content"}},
        "executor",
        "demo",
        profile,
    )
    assert record["payload"]["fields"]["short_description"] == candidate["summary"]
    assert record["payload"]["confirmation_id"] == draft["confirmation_id"]
    assert store.prepare(draft["confirmation_id"], "executor", "demo", profile) == record
    with pytest.raises(DeliveryError):
        confirm(store, profile, draft, candidate)
    assert [event["action"] for event in store.events(draft["id"], "executor", "demo", profile)] == [
        "created",
        "confirmed",
        "prepared",
    ]


def test_revision_invalidates_confirmation_and_rejects_stale_update(store, profile, candidate):
    draft = confirm(store, profile, create(store, profile, candidate), candidate)
    revised = store.revise(
        draft["id"], draft["revision"], {"source": SOURCE, "candidate": candidate}, "executor", "demo", profile
    )
    assert revised["confirmation_id"] is None
    with pytest.raises(DeliveryError):
        store.prepare(draft["confirmation_id"], "executor", "demo", profile)
    with pytest.raises(DeliveryError):
        confirm(store, profile, draft, candidate)


@pytest.mark.parametrize(
    "actor,name,tenant",
    [
        ("another", "demo", "synthetic-operations"),
        ("executor", "other", "synthetic-operations"),
        ("executor", "demo", "other-tenant"),
    ],
)
def test_draft_owner_profile_and_tenant_boundary(store, profile, candidate, actor, name, tenant):
    draft = create(store, profile, candidate)
    changed = profile.model_copy(update={"tenant": tenant})
    with pytest.raises(DeliveryError):
        store.get(draft["id"], actor, name, changed)


def test_connection_policy_change_requires_new_review(store, profile, candidate):
    draft = create(store, profile, candidate)
    changed = profile.model_copy(update={"base_url": "https://other-synthetic.service-now.com"})
    with pytest.raises(DeliveryError):
        store.get(draft["id"], "executor", "demo", changed)


def test_missing_fact_and_invalid_human_urgency_block_confirmation(store, profile, candidate):
    draft = create(store, profile, candidate)
    incomplete = {**candidate, "facts": {"asset_ref": None}, "evidence": []}
    with pytest.raises(DeliveryError):
        confirm(store, profile, draft, incomplete)
    for urgency in [None, True, 0, 4]:
        with pytest.raises(ValidationError):
            store.confirm(
                draft["id"],
                draft["revision"],
                {"candidate": candidate, "urgency": urgency},
                "executor",
                "demo",
                profile,
            )


def test_preparation_conflict_rolls_back_confirmation_freeze(store, profile, candidate):
    first = confirm(store, profile, create(store, profile, candidate), candidate)
    store.prepare(first["confirmation_id"], "executor", "demo", profile)
    second = confirm(store, profile, create(store, profile, candidate), candidate)
    with pytest.raises(DeliveryError):
        store.prepare(second["confirmation_id"], "executor", "demo", profile)
    assert store.get(second["id"], "executor", "demo", profile)["state"] == "confirmed"


def test_concurrent_confirm_has_one_winner(store, profile, candidate):
    draft = create(store, profile, candidate)

    def decide(_):
        try:
            confirm(store, profile, draft, candidate)
            return "confirmed"
        except DeliveryError:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(decide, range(2))) == ["confirmed", "conflict"]
    with store.engine.connect() as conn:
        assert conn.execute(select(drafts.c.revision)).scalar_one() == 2


class StubModel:
    def __init__(self, parsed=None, delay=0, error=None):
        self.parsed, self.delay, self.error, self.calls = parsed, delay, error, 0

    def with_structured_output(self, schema, include_raw=True):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error:
            raise ValueError(self.error)
        return {"parsed": self.parsed, "raw": SimpleNamespace(usage_metadata={"total_tokens": 30})}


@pytest.mark.asyncio
async def test_bounded_model_call_and_usage(candidate):
    model = StubModel(candidate)
    result = await extract_candidate(SOURCE, model)
    assert model.calls == 1
    assert result["model_run"]["usage"] == {"total_tokens": 30}
    assert result["model_run"]["error"] is None


@pytest.mark.asyncio
async def test_timeout_becomes_manual_draft_without_retry():
    model = StubModel(delay=0.1)
    result = await extract_candidate(SOURCE, model, timeout=0.01)
    assert result["model_run"]["error"] == "model_timeout"
    assert model.calls == 1


@pytest.mark.asyncio
async def test_provider_error_does_not_leak_message():
    result = await extract_candidate(SOURCE, StubModel(error="synthetic-secret-must-not-be-recorded"))
    assert "synthetic-secret" not in json.dumps(result)
    assert result["candidate"]["facts"]["asset_ref"] is None


@pytest.mark.asyncio
async def test_deepseek_request_schema_and_credential_scope(candidate):
    def handler(request):
        payload = json.loads(request.content)
        assert request.url.host == "api.deepseek.com"
        assert payload["messages"][-1]["role"] == "user"
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(candidate, ensure_ascii=False)}}
                ],
                "usage": {"total_tokens": 45},
            },
        )

    result = await extract_candidate(
        SOURCE, DeepSeekModel("synthetic-test-only", transport=httpx.MockTransport(handler))
    )
    assert result["model_run"]["error"] is None
    assert "synthetic-test-only" not in json.dumps(result)
