"""Langflow nodes for customer contract, dry-run and approved ServiceNow writeback."""

from __future__ import annotations

import asyncio
import json
import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, DictInput, MessageTextInput, Output, SecretStrInput
from lfx.schema.data import Data
from lfx.utils.ssrf_protection import validate_and_resolve_url
from lfx.utils.ssrf_transport import create_ssrf_protected_client, pin_host_for_url
from lfx.components.enterprise_delivery.connector import write_incident
from lfx.components.enterprise_delivery.contracts import (
    ConnectionProfile,
    DeliveryError,
    Ticket,
    normalize,
    prepare_change,
)
from lfx.components.enterprise_delivery.journal import Journal


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
        ticket = Ticket.model_validate(self.ticket.data)
        profile = load_profile(self.profile_name)
        profile.authorize(str(self.user_id), ticket.tenant)
        change = prepare_change(ticket, str(self.user_id), profile.audience)
        journal = journal_for(os.environ["SERVICEBRIDGE_DATABASE_URL"])
        record = await asyncio.to_thread(journal.prepare, change)
        return Data(data={"change": change, "state": record["state"], "external_write_performed": False})


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
