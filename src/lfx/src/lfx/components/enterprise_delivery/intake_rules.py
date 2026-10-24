"""Narrow deterministic baseline and clearly labelled offline demo; this is not an LLM."""

from __future__ import annotations

import re
import time

from lfx.components.enterprise_delivery.candidate_contracts import TicketCandidate, source_text


def extract_with_rules(raw: str) -> dict:
    start = time.monotonic()
    source = source_text(raw)
    patterns = {
        "asset_ref": r"SYN-A\d{3}|A17",
        "symptom": r"(?:反复报|出现|显示)\s*[EW]\d{2}",
        "attempted_action": r"重启[一二三\d]+次(?:仍未恢复|未恢复)?",
        "business_impact": r"影响\d+名前台人员无法收款",
        "workaround": r"暂无替代设备",
    }
    facts, evidence = {}, []
    for field, pattern in patterns.items():
        match = re.search(pattern, source)
        value = match.group() if match else None
        if field == "business_impact" and any(marker in source for marker in ("说法不一致", "存在两种说法")):
            value = None
        facts[field] = value
        if value:
            evidence.append({"field": field, "quote": value})
    summary = " / ".join(filter(None, (facts["asset_ref"], facts["symptom"]))) or None
    detail = "；".join(value for value in facts.values() if value) or None
    candidate = TicketCandidate.model_validate(
        {"summary": summary, "detail": detail, "facts": facts, "evidence": evidence}
    )
    return {
        "source": source,
        "candidate": candidate.model_dump(),
        "model_run": {
            "mode": "rules",
            "model": "narrow-rules-v1",
            "prompt_version": None,
            "calls": 0,
            "usage": {},
            "error": None,
            "elapsed_ms": round((time.monotonic() - start) * 1000, 3),
        },
    }
