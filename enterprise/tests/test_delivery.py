import os
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lfx.components.enterprise_delivery.connector import write_incident
from lfx.components.enterprise_delivery.contracts import (
    ConnectionProfile,
    DeliveryError,
    Ticket,
    issue_grant,
    normalize,
    prepare_change,
    verify_grant,
)
from lfx.components.enterprise_delivery.journal import Journal, metadata
from sqlalchemy import create_engine


@pytest.fixture
def keys():
    private = Ed25519PrivateKey.generate()
    return (
        private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode(),
    )


@pytest.fixture
def ticket():
    return Ticket(
        tenant="synthetic-operations",
        case_ref="SYN-001",
        customer_ref="SYN-CUSTOMER",
        summary="Synthetic incident",
        detail="Synthetic service request",
        urgency=2,
    )


@pytest.fixture
def change(ticket):
    return prepare_change(ticket, "SYN-EXECUTOR", "servicenow:synthetic")


@pytest.fixture
def profile(keys):
    return ConnectionProfile(
        tenant="synthetic-operations",
        audience="servicenow:synthetic",
        base_url="https://synthetic.service-now.com",
        token_env="SERVICEBRIDGE_TEST_TOKEN",
        allowed_user_ids=["SYN-EXECUTOR"],
        review_public_key=keys[1],
    )


@pytest.fixture
def journal():
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    metadata.drop_all(engine)
    metadata.create_all(engine)
    yield Journal(engine)
    metadata.drop_all(engine)
    engine.dispose()


def test_contract_scrubs_contact_and_drops_extra_source(ticket):
    source = ticket.model_dump()
    source.update(summary="Contact user001@example.invalid", raw_contact="sensitive unused source")
    normalized = normalize(source, {key: key for key in Ticket.model_fields})
    assert "@" not in normalized.summary
    assert "raw_contact" not in normalized.model_dump()


def test_mapping_requires_all_fields(ticket):
    with pytest.raises(DeliveryError):
        normalize(ticket.model_dump(), {"summary": "summary"})


def test_external_grant_is_bound_to_payload_and_audience(change, keys):
    grant = issue_grant(change, "SYN-REVIEWER", keys[0], now=1000)
    assert verify_grant(change, grant, keys[1], now=1100).reviewer_id == "SYN-REVIEWER"
    for modification in ({"digest": "0" * 64}, {"tenant": "another"}, {"audience": "another"}):
        with pytest.raises(DeliveryError):
            verify_grant({**change, **modification}, grant, keys[1], now=1100)


def test_expired_and_tampered_grants_are_rejected(change, keys):
    grant = issue_grant(change, "SYN-REVIEWER", keys[0], now=1000)
    with pytest.raises(DeliveryError):
        verify_grant(change, grant, keys[1], now=1300)
    with pytest.raises(DeliveryError):
        verify_grant(change, grant[:-8] + "tampered", keys[1], now=1100)


def test_self_approval_is_rejected(change, keys):
    with pytest.raises(DeliveryError):
        issue_grant(change, change["requester_id"], keys[0])


@pytest.mark.parametrize("user,tenant", [("other-user", "synthetic-operations"), ("SYN-EXECUTOR", "other-tenant")])
def test_connection_profile_does_not_widen_access(profile, user, tenant):
    with pytest.raises(DeliveryError):
        profile.authorize(user, tenant)


def test_profile_rejects_arbitrary_or_credentialed_url(profile):
    for url in [
        "http://127.0.0.1",
        "https://attacker.example",
        "https://name:password@synthetic.service-now.com",
        "https://synthetic.service-now.com/other",
    ]:
        with pytest.raises(DeliveryError):
            profile.model_copy(update={"base_url": url}).authorize("SYN-EXECUTOR", profile.tenant)


def test_journal_prevents_changed_customer_case(journal, change):
    assert journal.prepare(change)["digest"] == change["digest"]
    assert journal.prepare(change)["digest"] == change["digest"]
    with pytest.raises(DeliveryError):
        journal.prepare({**change, "digest": "f" * 64})


def test_concurrent_claim_has_one_winner(journal, change):
    journal.prepare(change)

    def claim(_):
        try:
            journal.claim(change["digest"])
            return "claimed"
        except DeliveryError:
            return "blocked"

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(claim, range(2))) == ["blocked", "claimed"]


@pytest.mark.asyncio
async def test_successful_retry_replays_receipt_without_new_post(journal, change, profile, keys):
    journal.prepare(change)
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(
            200 if request.method == "GET" else 201,
            json={"result": [] if request.method == "GET" else {"sys_id": "a" * 32}},
        )

    grant = issue_grant(change, "SYN-REVIEWER", keys[0])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await write_incident(journal, change, grant, profile, "SYN-EXECUTOR", "synthetic-test-token", client)
        assert receipt == await write_incident(
            journal, change, grant, profile, "SYN-EXECUTOR", "synthetic-test-token", client
        )
    assert calls == ["GET", "POST"]


@pytest.mark.asyncio
async def test_timeout_after_post_requires_reconciliation(journal, change, profile, keys):
    journal.prepare(change)
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            raise httpx.ReadTimeout("Synthetic timeout")
        return httpx.Response(200, json={"result": []})

    grant = issue_grant(change, "SYN-REVIEWER", keys[0])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        for _ in range(2):
            with pytest.raises(DeliveryError):
                await write_incident(journal, change, grant, profile, "SYN-EXECUTOR", "synthetic-test-token", client)
    assert journal.get(change["digest"])["state"] == "uncertain"
    assert calls == ["GET", "POST"]


@pytest.mark.asyncio
async def test_preflight_timeout_can_retry_without_prior_write(journal, change, profile, keys):
    journal.prepare(change)

    def handler(request):
        raise httpx.ConnectTimeout("Synthetic preflight timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DeliveryError):
            await write_incident(
                journal,
                change,
                issue_grant(change, "SYN-REVIEWER", keys[0]),
                profile,
                "SYN-EXECUTOR",
                "synthetic-test-token",
                client,
            )
    assert journal.get(change["digest"])["state"] == "prepared"
