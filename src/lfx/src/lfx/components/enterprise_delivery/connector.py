from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from lfx.components.enterprise_delivery.contracts import ConnectionProfile, DeliveryError, verify_grant
from lfx.components.enterprise_delivery.journal import Journal


def _record_id(result: Any) -> str:
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("sys_id"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", result["sys_id"])
    ):
        raise DeliveryError("ServiceNow did not return a valid record identifier")
    return result["sys_id"]


async def write_incident(
    journal: Journal,
    change: dict[str, Any],
    grant: str,
    profile: ConnectionProfile,
    user_id: str,
    access_token: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    profile.authorize(user_id, change["tenant"])
    if user_id != change["requester_id"] or not access_token:
        raise DeliveryError("The writeback must use its prepared execution user and a provisioned credential")
    stored = await asyncio.to_thread(journal.get, change["digest"])
    if stored["payload"] != change:
        raise DeliveryError("The change differs from its prepared journal entry")
    if change["audience"] != profile.audience or change["action"] != "servicenow.incident.create":
        raise DeliveryError("The change is not approved for this connector")
    verify_grant(change, grant, profile.review_public_key)
    claimed = await asyncio.to_thread(journal.claim, change["digest"])
    if claimed["state"] == "completed":
        return claimed["receipt"]
    attempted_write = False
    headers = {"Authorization": "Bearer " + access_token, "Accept": "application/json"}
    endpoint = profile.base_url.rstrip("/") + "/api/now/table/incident"
    try:
        lookup = await client.get(
            endpoint,
            headers=headers,
            params={
                "sysparm_query": "correlation_id=" + change["correlation_id"],
                "sysparm_fields": "sys_id,correlation_id",
                "sysparm_limit": "2",
            },
            follow_redirects=False,
        )
        lookup.raise_for_status()
        found = lookup.json()["result"]
        if not isinstance(found, list) or len(found) > 1:
            await asyncio.to_thread(journal.finish, change["digest"], "uncertain")
            raise DeliveryError("Correlation lookup is ambiguous; reconcile with the customer")
        if found:
            remote_id = _record_id(found[0])
        else:
            # Only standard Incident fields are sent. Customer-specific fields need a reviewed mapping.
            fields = {key: change["fields"][key] for key in ("short_description", "description", "urgency")}
            fields["correlation_id"] = change["correlation_id"]
            attempted_write = True
            response = await client.post(endpoint, headers=headers, json=fields, follow_redirects=False)
            if 400 <= response.status_code < 500:
                await asyncio.to_thread(journal.finish, change["digest"], "failed")
                raise DeliveryError("ServiceNow rejected the write; an operator must inspect the profile")
            response.raise_for_status()
            remote_id = _record_id(response.json()["result"])
        receipt = {"sys_id": remote_id, "correlation_id": change["correlation_id"], "digest": change["digest"]}
        await asyncio.to_thread(journal.finish, change["digest"], "completed", receipt)
        return receipt
    except Exception as exc:
        latest = await asyncio.to_thread(journal.get, change["digest"])
        if latest["state"] == "sending":
            await asyncio.to_thread(journal.finish, change["digest"], "uncertain" if attempted_write else "prepared")
        raise DeliveryError("Delivery did not complete; check the journal before any retry") from exc
