"""Loopback-only synthetic demo. External approval and ServiceNow are simulated."""

from __future__ import annotations

import argparse
import hashlib
import secrets
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from lfx.components.enterprise_delivery.candidate_model import extract_candidate_sync
from lfx.components.enterprise_delivery.candidate_store import CandidateStore, prepare_flow_input
from lfx.components.enterprise_delivery.connector import write_incident
from lfx.components.enterprise_delivery.contracts import ConnectionProfile, DeliveryError, issue_grant
from lfx.components.enterprise_delivery.deepseek_model import DeepSeekModel
from lfx.components.enterprise_delivery.intake_rules import extract_with_rules
from lfx.components.enterprise_delivery.journal import Journal, metadata
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import create_engine


class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=12000)
    draft_id: str | None = None
    revision: int = Field(default=1, ge=1)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str
    revision: int = Field(ge=1)
    final_content: dict


class ReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_id: str | None = None
    digest: str | None = None
    grant: str | None = None
    scenario: str = "success"


def create_app(database_url: str, *, model=None, test_host: bool = False) -> FastAPI:
    """Only call this synthetic harness with its own disposable SQLite database."""
    if not database_url.startswith("sqlite:"):
        raise ValueError("The demo requires a separate disposable SQLite database")
    engine = create_engine(database_url)
    metadata.create_all(engine)  # Explicit demo initialization, never part of production model calls.
    store, journal = CandidateStore(engine), Journal(engine)
    demo_token = secrets.token_urlsafe(32)
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    actor, reviewer, profile_name = "SYN-EXECUTOR", "SYN-REVIEWER", "synthetic-demo"
    profile = ConnectionProfile(
        tenant="synthetic-operations",
        audience="servicenow:synthetic",
        base_url="https://synthetic.service-now.com",
        token_env="SYNTHETIC_DEMO_TOKEN",
        allowed_user_ids=[actor],
        review_public_key=public,
    )
    remote_records: dict[str, str] = {}
    counters = {"get": 0, "post": 0}
    app = FastAPI(title="ServiceBridge local synthetic demo", docs_url=None, redoc_url=None)
    app.state.demo_token, app.state.engine = demo_token, engine
    app.state.store, app.state.profile, app.state.counters = store, profile, counters

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = urlsplit("http://" + request.headers.get("host", "")).hostname
        allowed_hosts = {"127.0.0.1", "localhost"} | ({"testserver"} if test_host else set())
        if host not in allowed_hosts:
            return JSONResponse({"error": "Loopback demo only"}, status_code=403)
        if request.method != "GET":
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"error": "Cross-origin request rejected"}, status_code=403)
            if not secrets.compare_digest(request.headers.get("x-demo-token", ""), demo_token):
                return JSONResponse({"error": "Demo session token required"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(DeliveryError)
    async def delivery_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(ValidationError)
    async def content_error(request, exc):
        return JSONResponse({"error": "字段或候选内容不符合契约，请检查必填项和原文引用。"}, status_code=422)

    @app.get("/", response_class=HTMLResponse)
    def index():
        page = (Path(__file__).parent / "static" / "review.html").read_text(encoding="utf-8")
        return HTMLResponse(page.replace("__DEMO_TOKEN__", demo_token), headers={"Cache-Control": "no-store"})

    @app.get("/api/config")
    def config():
        return {
            "model_mode": "deepseek-flash" if model else "rules-offline",
            "external_system": "simulated-servicenow",
            "approval": "simulated-independent-reviewer",
        }

    @app.post("/api/intake")
    def intake(value: IntakeRequest):
        if value.draft_id:
            store.get(value.draft_id, actor, profile_name, profile)
        result = (
            extract_candidate_sync(value.source, model, model_label="deepseek-flash")
            if model
            else extract_with_rules(value.source)
        )
        if value.draft_id:
            return store.revise(value.draft_id, value.revision, result, actor, profile_name, profile)
        return store.create(result, actor, profile_name, profile, "SYN-" + uuid.uuid4().hex[:12], "SYN-CUSTOMER")

    @app.post("/api/confirm")
    def confirm(value: ConfirmRequest):
        return store.confirm(value.draft_id, value.revision, value.final_content, actor, profile_name, profile)

    @app.post("/api/prepare")
    def prepare(value: ReferenceRequest):
        record = prepare_flow_input(journal, {"confirmation_id": value.confirmation_id}, actor, profile_name, profile)
        return {"change": record["payload"], "state": record["state"], "external_write_performed": False}

    @app.post("/api/approve")
    def approve(value: ReferenceRequest):
        record = journal.get(value.digest or "")
        return {"grant": issue_grant(record["payload"], reviewer, private), "reviewer": reviewer}

    @app.post("/api/deliver")
    async def deliver(value: ReferenceRequest):
        if value.scenario not in {"success", "post-timeout"}:
            raise DeliveryError("Unknown synthetic scenario")
        record = journal.get(value.digest or "")

        def remote(request):
            if request.method == "GET":
                counters["get"] += 1
                correlation = request.url.params["sysparm_query"].removeprefix("correlation_id=")
                found = remote_records.get(correlation)
                return httpx.Response(200, json={"result": [{"sys_id": found}] if found else []})
            counters["post"] += 1
            import json

            fields = json.loads(request.content)
            correlation = fields["correlation_id"]
            remote_id = hashlib.sha256(correlation.encode()).hexdigest()[:32]
            remote_records[correlation] = remote_id
            if value.scenario == "post-timeout":
                raise httpx.ReadTimeout("Synthetic response loss after simulated creation")
            return httpx.Response(201, json={"result": {"sys_id": remote_id}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(remote)) as client:
            receipt = await write_incident(
                journal, record["payload"], value.grant or "", profile, actor, "synthetic-demo-only", client
            )
        return {"receipt": receipt, "state": journal.get(record["digest"])["state"], "external_system": "simulated"}

    @app.post("/api/reconcile")
    def reconcile(value: ReferenceRequest):
        record = journal.get(value.digest or "")
        return {
            "local_state": record["state"],
            "simulated_remote_sys_id": remote_records.get(record["payload"]["correlation_id"]),
            "automatic_retry_allowed": False,
            "note": "查询结果仅供核对，不会自动修改 uncertain 或重新发送。",
        }

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--mode", choices=["rules", "deepseek"], default="rules")
    parser.add_argument("--port", type=int, default=7868)
    args = parser.parse_args()
    args.database.parent.mkdir(parents=True, exist_ok=True)
    live_model = DeepSeekModel.from_env() if args.mode == "deepseek" else None
    uvicorn.run(
        create_app("sqlite:///" + str(args.database.resolve()), model=live_model),
        host="127.0.0.1",
        port=args.port,
        access_log=False,
    )
