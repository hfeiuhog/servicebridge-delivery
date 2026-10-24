"""Langflow nodes for customer contract, dry-run and approved ServiceNow writeback."""

from __future__ import annotations

import asyncio
import json
import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine

from lfx.components.enterprise_delivery.candidate_model import extract_candidate
from lfx.components.enterprise_delivery.candidate_store import CandidateStore, prepare_flow_input
from lfx.components.enterprise_delivery.connector import write_incident
from lfx.components.enterprise_delivery.contracts import (
    ConnectionProfile,
    DeliveryError,
    normalize,
)
from lfx.components.enterprise_delivery.deepseek_model import DeepSeekModel
from lfx.components.enterprise_delivery.journal import Journal
from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, DictInput, HandleInput, IntInput, MessageTextInput, MultilineInput, Output, SecretStrInput
from lfx.schema.data import Data
from lfx.utils.ssrf_protection import validate_and_resolve_url
from lfx.utils.ssrf_transport import create_ssrf_protected_client, pin_host_for_url


def load_profile(name: str) -> ConnectionProfile:
    profiles = json.loads(Path(os.environ["SERVICEBRIDGE_PROFILES_FILE"]).read_text(encoding="utf-8"))
    if name not in profiles:
        raise DeliveryError("The operator has not provisioned this connection profile")
    return ConnectionProfile.model_validate(profiles[name])


@lru_cache(maxsize=4)
def journal_for(url: str) -> Journal:
    if not url.startswith("postgresql"):
        raise DeliveryError("Provision a PostgreSQL delivery journal for the platform")
    return Journal(create_engine(url, pool_pre_ping=True))


class EnterpriseTicketContract(Component):
    display_name = "Enterprise Ticket Contract"
    description = "Map an authorized customer record to a strict, scrubbed service ticket."
    icon = "ClipboardCheck"
    name = "EnterpriseTicketContract"
    inputs = [
        DataInput(name="source_record", display_name="Customer Record", required=True),
        DictInput(
            name="field_mapping",
            display_name="Field Mapping",
            required=True,
            value={key: key for key in ("tenant", "case_ref", "customer_ref", "summary", "detail", "urgency")},
        ),
    ]
    outputs = [Output(display_name="Validated Ticket", name="ticket", method="validate_ticket")]

    def validate_ticket(self) -> Data:
        ticket = normalize(self.source_record.data, self.field_mapping)
        return Data(data=ticket.model_dump())


class EnterprisePrepareChange(Component):
    display_name = "Enterprise Prepare Change"
    description = "Create a durable dry-run package for an independent reviewer. No external write occurs."
    icon = "FileCheck"
    name = "EnterprisePrepareChange"
    inputs = [
        DataInput(name="ticket", display_name="Validated Ticket", required=True),
        MessageTextInput(name="profile_name", display_name="Provisioned Profile", required=True),
    ]
    outputs = [Output(display_name="Review Package", name="proposal", method="prepare")]

    async def prepare(self) -> Data:
        if self.user_id is None:
            raise DeliveryError("Authenticated execution context is required")
        profile = load_profile(self.profile_name)
        journal = journal_for(os.environ["SERVICEBRIDGE_DATABASE_URL"])
        record = await asyncio.to_thread(
            prepare_flow_input,
            journal,
            self.ticket.data,
            str(self.user_id),
            self.profile_name,
            profile,
            allow_legacy=os.environ.get("SERVICEBRIDGE_ALLOW_LEGACY_UNCONFIRMED") == "true",
        )
        return Data(data={"change": record["payload"], "state": record["state"], "external_write_performed": False})


class EnterpriseServiceNowWriteback(Component):
    display_name = "Enterprise ServiceNow Writeback"
    description = "Apply an externally approved proposal using a scoped connection profile and durable journal."
    icon = "Cable"
    name = "EnterpriseServiceNowWriteback"
    inputs = [
        DataInput(name="proposal", display_name="Review Package", required=True),
        MessageTextInput(name="profile_name", display_name="Provisioned Profile", required=True),
        SecretStrInput(name="review_grant", display_name="External Review Grant", required=True),
    ]
    outputs = [Output(display_name="Delivery Receipt", name="receipt", method="deliver")]

    async def deliver(self) -> Data:
        if self.user_id is None:
            raise DeliveryError("Authenticated execution context is required")
        change = self.proposal.data["change"]
        profile = load_profile(self.profile_name)
        profile.authorize(str(self.user_id), change["tenant"])
        token = os.environ[profile.token_env]
        journal = journal_for(os.environ["SERVICEBRIDGE_DATABASE_URL"])
        _, addresses = await asyncio.to_thread(validate_and_resolve_url, profile.base_url)
        if not addresses:
            raise DeliveryError("The connector requires active DNS-pinned SSRF validation")
        async with create_ssrf_protected_client(
            pin_host_for_url(profile.base_url), addresses, timeout=20, follow_redirects=False, trust_env=False
        ) as client:
            receipt = await write_incident(
                journal, change, self.review_grant, profile, str(self.user_id), token, client
            )
        return Data(data=receipt)


class EnterpriseTicketIntake(Component):
    display_name = "Enterprise Ticket Intake"
    description = "Generate an editable, source-grounded candidate. No external business write occurs."
    icon = "ClipboardList"
    name = "EnterpriseTicketIntake"
    inputs = [
        MultilineInput(name="source_text", display_name="Authorized Source Text", required=True),
        MessageTextInput(name="profile_name", display_name="Provisioned Profile", required=True),
        MessageTextInput(name="case_ref", display_name="Controlled Case Reference", required=True),
        MessageTextInput(name="customer_ref", display_name="Controlled Customer Reference", required=True),
        HandleInput(
            name="language_model", display_name="Optional Language Model", input_types=["LanguageModel"], required=False
        ),
        MessageTextInput(name="draft_id", display_name="Draft ID to Revise", advanced=True),
        IntInput(name="revision", display_name="Expected Revision", value=1, advanced=True),
    ]
    outputs = [Output(display_name="Candidate and Questions", name="draft", method="generate")]

    async def generate(self) -> Data:
        if self.user_id is None:
            raise DeliveryError("Authenticated execution context is required")
        actor = str(self.user_id)
        profile = load_profile(self.profile_name)
        profile.authorize(actor, profile.tenant)
        store = CandidateStore(journal_for(os.environ["SERVICEBRIDGE_DATABASE_URL"]).engine)
        draft_id = getattr(self, "draft_id", None)
        if draft_id:
            existing = await asyncio.to_thread(store.get, draft_id, actor, self.profile_name, profile)
            if (existing["case_ref"], existing["customer_ref"]) != (self.case_ref, self.customer_ref):
                raise DeliveryError("Create a new draft when controlled identifiers change")
        model = getattr(self, "language_model", None)
        if model is None and os.environ.get("DEEPSEEK_API_KEY"):
            model = DeepSeekModel.from_env()
        label = getattr(model, "model", None) or "connected-language-model"
        result = await extract_candidate(self.source_text, model, model_label=str(label))
        if draft_id:
            record = await asyncio.to_thread(
                store.revise, draft_id, self.revision, result, actor, self.profile_name, profile
            )
        else:
            record = await asyncio.to_thread(
                store.create, result, actor, self.profile_name, profile, self.case_ref, self.customer_ref
            )
        return Data(data=record)


class EnterpriseConfirmTicket(Component):
    display_name = "Enterprise Confirm Ticket"
    description = "Persist an authenticated content confirmation bound to the current source revision."
    icon = "UserCheck"
    name = "EnterpriseConfirmTicket"
    inputs = [
        DataInput(name="draft", display_name="Current Candidate Draft", required=True),
        DictInput(name="final_content", display_name="Reviewed Candidate and Human Urgency", required=True),
        MessageTextInput(name="profile_name", display_name="Provisioned Profile", required=True),
    ]
    outputs = [Output(display_name="Persisted Confirmation", name="ticket", method="confirm")]

    async def confirm(self) -> Data:
        if self.user_id is None:
            raise DeliveryError("Authenticated execution context is required")
        profile = load_profile(self.profile_name)
        store = CandidateStore(journal_for(os.environ["SERVICEBRIDGE_DATABASE_URL"]).engine)
        record = await asyncio.to_thread(
            store.confirm,
            self.draft.data["id"],
            self.draft.data["revision"],
            self.final_content,
            str(self.user_id),
            self.profile_name,
            profile,
        )
        return Data(
            data={
                "confirmation_id": record["confirmation_id"],
                "draft_id": record["id"],
                "revision": record["revision"],
            }
        )
