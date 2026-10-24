"""Untrusted model suggestions and human-confirmed ticket content."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lfx.components.enterprise_delivery.contracts import DeliveryError, scrub

FactName = Literal["asset_ref", "symptom", "attempted_action", "business_impact", "workaround"]
REQUIRED_FACTS = ("asset_ref", "symptom", "business_impact")
QUESTIONS = {
    "asset_ref": "请补充故障设备或服务的编号。",
    "symptom": "请说明观察到的故障现象或错误代码。",
    "business_impact": "目前影响哪些业务或使用者，是否完全阻断？",
}


class IntakeFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    asset_ref: str | None = Field(default=None, min_length=1, max_length=300)
    symptom: str | None = Field(default=None, min_length=1, max_length=1000)
    attempted_action: str | None = Field(default=None, min_length=1, max_length=1000)
    business_impact: str | None = Field(default=None, min_length=1, max_length=1000)
    workaround: str | None = Field(default=None, min_length=1, max_length=1000)


class FactEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    field: FactName
    quote: str = Field(min_length=1, max_length=1000)


class TicketCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    summary: str | None = Field(default=None, min_length=1, max_length=160)
    detail: str | None = Field(default=None, min_length=1, max_length=4000)
    facts: IntakeFacts = Field(default_factory=IntakeFacts)
    evidence: list[FactEvidence] = Field(default_factory=list, max_length=30)


class ConfirmContent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    candidate: TicketCandidate
    urgency: int = Field(ge=1, le=3)


def source_text(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 12000:
        raise DeliveryError("Provide 1-12000 characters of authorized intake text")
    return scrub(raw.strip())


def content_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def validate_candidate(source: str, candidate: TicketCandidate | dict) -> TicketCandidate:
    result = TicketCandidate.model_validate(candidate)
    supported = set()
    for item in result.evidence:
        if item.quote not in source:
            raise DeliveryError("Evidence must quote the current, scrubbed source exactly")
        if getattr(result.facts, item.field) is None:
            raise DeliveryError("Evidence cannot target an absent fact")
        supported.add(item.field)
    for field, value in result.facts.model_dump().items():
        if value is not None and field not in supported:
            raise DeliveryError("Every supplied fact needs a source quote")
    facts = result.facts.model_dump()
    unknown = {"unknown", "not specified", "n/a", "未知", "不详", "未提供", "待确认"}
    removed = set()
    for field, value in facts.items():
        if value is None:
            continue
        unresolved = value.lower().strip() in unknown
        if field == "business_impact":
            unresolved = unresolved or any(
                marker in value
                for marker in (
                    "尚未确认",
                    "未确认",
                    "不清楚",
                    "不确定",
                    "未说明",
                    "说法不一致",
                    "存在两种说法",
                )
            )
        if unresolved:
            facts[field] = None
            removed.add(field)
    if removed:
        result = result.model_copy(
            update={
                "facts": IntakeFacts.model_validate(facts),
                "evidence": [item for item in result.evidence if item.field not in removed],
            }
        )
    # A matching quote establishes location, not semantic entailment. Human review remains required.
    return result


def missing_information(candidate: TicketCandidate) -> list[str]:
    return [name for name in REQUIRED_FACTS if getattr(candidate.facts, name) is None]


def follow_up_questions(candidate: TicketCandidate) -> list[str]:
    return [QUESTIONS[name] for name in missing_information(candidate)]
