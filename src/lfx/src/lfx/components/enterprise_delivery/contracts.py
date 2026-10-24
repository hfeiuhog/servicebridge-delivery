"""Customer contracts and external review grants for service delivery."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field


class DeliveryError(ValueError):
    pass


def scrub(value: str) -> str:
    value = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL]", value)
    return re.sub(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", "[PHONE]", value)


class Ticket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant: str = Field(pattern=r"^[A-Za-z0-9_-]{1,60}$")
    case_ref: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    customer_ref: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    summary: str = Field(min_length=1, max_length=160)
    detail: str = Field(min_length=1, max_length=4000)
    urgency: int = Field(ge=1, le=3)


class ConnectionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant: str
    audience: str
    base_url: str
    token_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{4,100}$")
    allowed_user_ids: list[str] = Field(min_length=1)
    review_public_key: str

    def authorize(self, user_id: str, tenant: str) -> None:
        host = urlsplit(self.base_url)
        if host.scheme != "https" or not host.hostname or not host.hostname.endswith(".service-now.com"):
            raise DeliveryError("Use an operator-approved HTTPS ServiceNow origin")
        if (
            host.username
            or host.password
            or host.query
            or host.fragment
            or host.path not in {"", "/"}
            or host.port not in {None, 443}
        ):
            raise DeliveryError("The connection profile must contain only a ServiceNow origin")
        if tenant != self.tenant or user_id not in self.allowed_user_ids:
            raise DeliveryError("The execution user or tenant cannot use this profile")


def normalize(source: dict[str, Any], mapping: dict[str, str]) -> Ticket:
    allowed = set(Ticket.model_fields)
    if set(mapping) != allowed:
        raise DeliveryError("Field mapping must cover the canonical ticket fields exactly")
    try:
        mapped = {target: source[field] for target, field in mapping.items()}
    except KeyError as exc:
        raise DeliveryError("A required customer field is missing") from exc
    ticket = Ticket.model_validate(mapped)
    return ticket.model_copy(update={"summary": scrub(ticket.summary), "detail": scrub(ticket.detail)})


def prepare_change(
    ticket: Ticket, requester_id: str, audience: str, *, confirmation_id: str | None = None
) -> dict[str, Any]:
    if not requester_id:
        raise DeliveryError("An authenticated execution user is required")
    payload: dict[str, Any] = {
        "tenant": ticket.tenant,
        "case_ref": ticket.case_ref,
        "requester_id": requester_id,
        "audience": audience,
        "action": "servicenow.incident.create",
        "fields": {
            "short_description": scrub(ticket.summary),
            "description": scrub(ticket.detail),
            "urgency": str(ticket.urgency),
            "u_customer_reference": ticket.customer_ref,
        },
    }
    if confirmation_id is not None:
        payload["confirmation_id"] = confirmation_id
    # The custom customer field is not sent to ServiceNow by default.
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    payload["digest"] = digest
    payload["correlation_id"] = "sb-" + digest
    return payload


class Grant(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    digest: str
    tenant: str
    case_ref: str
    audience: str
    reviewer_id: str = Field(min_length=1, max_length=100)
    issued_at: int
    expires: int


def issue_grant(change: dict[str, Any], reviewer_id: str, private_key: bytes, now: int | None = None) -> str:
    current = int(time.time()) if now is None else now
    if reviewer_id == change["requester_id"]:
        raise DeliveryError("A different reviewer must approve the change")
    grant = Grant(
        digest=change["digest"],
        tenant=change["tenant"],
        case_ref=change["case_ref"],
        audience=change["audience"],
        reviewer_id=reviewer_id,
        issued_at=current,
        expires=current + 300,
    )
    key = serialization.load_pem_private_key(private_key, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise DeliveryError("Use an Ed25519 review key")
    raw = grant.model_dump_json().encode()
    signature = key.sign(b"servicebridge-review-v1\n" + raw)
    return base64.urlsafe_b64encode(raw).decode() + "." + base64.urlsafe_b64encode(signature).decode()


def verify_grant(change: dict[str, Any], token: str, public_key: str, now: int | None = None) -> Grant:
    if len(token) > 8192:
        raise DeliveryError("Approval grant is too large")
    try:
        data, signature = token.split(".")
        raw = base64.urlsafe_b64decode(data)
        key = serialization.load_pem_public_key(public_key.encode())
        if not isinstance(key, Ed25519PublicKey):
            raise DeliveryError("Use an Ed25519 public key")
        key.verify(base64.urlsafe_b64decode(signature), b"servicebridge-review-v1\n" + raw)
        grant = Grant.model_validate_json(raw)
    except Exception as exc:
        raise DeliveryError("Invalid external review grant") from exc
    current = int(time.time()) if now is None else now
    if not grant.issued_at <= current < grant.expires <= grant.issued_at + 300:
        raise DeliveryError("Review grant expired or has invalid timing")
    for name in ("digest", "tenant", "case_ref", "audience"):
        if grant.model_dump()[name] != change[name]:
            raise DeliveryError("The review grant is bound to another change")
    if grant.reviewer_id == change["requester_id"]:
        raise DeliveryError("Self-approval is not accepted")
    return grant
