"""Versioned intake, authenticated confirmation, and atomic execution handoff."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Column, ForeignKey, Integer, String, Table, insert, select, update
from sqlalchemy.engine import Connection, Engine

from lfx.components.enterprise_delivery.candidate_contracts import (
    ConfirmContent,
    TicketCandidate,
    content_hash,
    follow_up_questions,
    missing_information,
    source_text,
    validate_candidate,
)
from lfx.components.enterprise_delivery.contracts import ConnectionProfile, DeliveryError, Ticket, prepare_change
from lfx.components.enterprise_delivery.journal import Journal, deliveries, metadata

drafts = Table(
    "servicebridge_intake",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant", String(60), nullable=False),
    Column("owner_id", String(100), nullable=False),
    Column("case_ref", String(80), nullable=False),
    Column("customer_ref", String(80), nullable=False),
    Column("profile_name", String(100), nullable=False),
    Column("profile_hash", String(64), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("state", String(20), nullable=False),
    Column("source", String(12000), nullable=False),
    Column("source_hash", String(64), nullable=False),
    Column("candidate", JSON, nullable=False),
    Column("model_run", JSON, nullable=False),
    Column("confirmation_id", String(36), unique=True, nullable=True),
    Column("final_ticket", JSON, nullable=True),
    Column("prepared_digest", String(64), nullable=True),
    Column("updated_at", String(40), nullable=False),
)
intake_events = Table(
    "servicebridge_intake_event",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("draft_id", String(36), ForeignKey("servicebridge_intake.id"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("actor_id", String(100), nullable=False),
    Column("action", String(30), nullable=False),
    Column("recorded_at", String(40), nullable=False),
    Column("snapshot", JSON, nullable=False),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CandidateStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _read(conn: Connection, draft_id: str, actor: str, profile_name: str, profile: ConnectionProfile) -> dict:
        row = conn.execute(select(drafts).where(drafts.c.id == draft_id)).first()
        if row is None:
            raise DeliveryError("Intake draft not found")
        record = dict(row._mapping)
        profile.authorize(actor, record["tenant"])
        if record["owner_id"] != actor or record["profile_name"] != profile_name:
            raise DeliveryError("The execution identity cannot use this draft")
        if record["profile_hash"] != content_hash(profile.model_dump()):
            raise DeliveryError("Connection policy changed; create and review a new draft")
        return record

    @staticmethod
    def _event(conn: Connection, record: dict, actor: str, action: str) -> None:
        conn.execute(
            insert(intake_events).values(
                id=str(uuid.uuid4()),
                draft_id=record["id"],
                revision=record["revision"],
                actor_id=actor,
                action=action,
                recorded_at=_now(),
                snapshot={
                    "source_hash": record["source_hash"],
                    "candidate_hash": content_hash(record["candidate"]),
                    "confirmation_id": record.get("confirmation_id"),
                    "final_ticket_hash": content_hash(record.get("final_ticket")),
                },
            )
        )

    @staticmethod
    def _view(record: dict) -> dict:
        candidate = TicketCandidate.model_validate(record["candidate"])
        return {
            **record,
            "missing_information": missing_information(candidate),
            "questions": follow_up_questions(candidate),
        }

    def create(
        self,
        result: dict,
        actor: str,
        profile_name: str,
        profile: ConnectionProfile,
        case_ref: str,
        customer_ref: str,
    ) -> dict:
        profile.authorize(actor, profile.tenant)
        if not actor or len(actor) > 100 or not profile_name or len(profile_name) > 100:
            raise DeliveryError("Invalid execution identity or profile name")
        # Validate the controlled identifiers without filling any final business content.
        Ticket(
            tenant=profile.tenant,
            case_ref=case_ref,
            customer_ref=customer_ref,
            summary="identifier-validation",
            detail="identifier-validation",
            urgency=3,
        )
        source = source_text(result["source"])
        candidate = validate_candidate(source, result["candidate"])
        record = dict(
            id=str(uuid.uuid4()),
            tenant=profile.tenant,
            owner_id=actor,
            case_ref=case_ref,
            customer_ref=customer_ref,
            profile_name=profile_name,
            profile_hash=content_hash(profile.model_dump()),
            revision=1,
            state="draft",
            source=source,
            source_hash=content_hash(source),
            candidate=candidate.model_dump(),
            model_run=result.get("model_run", {}),
            confirmation_id=None,
            final_ticket=None,
            prepared_digest=None,
            updated_at=_now(),
        )
        with self.engine.begin() as conn:
            conn.execute(insert(drafts).values(**record))
            self._event(conn, record, actor, "created")
        return self._view(record)

    def get(self, draft_id: str, actor: str, profile_name: str, profile: ConnectionProfile) -> dict:
        with self.engine.connect() as conn:
            return self._view(self._read(conn, draft_id, actor, profile_name, profile))

    def revise(
        self,
        draft_id: str,
        revision: int,
        result: dict,
        actor: str,
        profile_name: str,
        profile: ConnectionProfile,
    ) -> dict:
        source = source_text(result["source"])
        candidate = validate_candidate(source, result["candidate"])
        with self.engine.begin() as conn:
            record = self._read(conn, draft_id, actor, profile_name, profile)
            changes = dict(
                source=source,
                source_hash=content_hash(source),
                candidate=candidate.model_dump(),
                model_run=result.get("model_run", {}),
                revision=revision + 1,
                state="draft",
                confirmation_id=None,
                final_ticket=None,
                updated_at=_now(),
            )
            count = conn.execute(
                update(drafts)
                .where(
                    drafts.c.id == draft_id,
                    drafts.c.revision == revision,
                    drafts.c.state.in_(["draft", "confirmed"]),
                )
                .values(**changes)
            ).rowcount
            if count != 1:
                raise DeliveryError("Draft changed or is already prepared; reload before editing")
            record.update(changes)
            self._event(conn, record, actor, "revised")
        return self._view(record)

    def confirm(
        self,
        draft_id: str,
        revision: int,
        final: ConfirmContent | dict,
        actor: str,
        profile_name: str,
        profile: ConnectionProfile,
    ) -> dict:
        final = ConfirmContent.model_validate(final)
        with self.engine.begin() as conn:
            record = self._read(conn, draft_id, actor, profile_name, profile)
            candidate = validate_candidate(record["source"], final.candidate)
            if missing_information(candidate) or not candidate.summary or not candidate.detail:
                raise DeliveryError("Review and supply the required facts, title and detail before confirmation")
            ticket = Ticket(
                tenant=record["tenant"],
                case_ref=record["case_ref"],
                customer_ref=record["customer_ref"],
                summary=candidate.summary,
                detail=candidate.detail,
                urgency=final.urgency,
            )
            changes = dict(
                revision=revision + 1,
                state="confirmed",
                candidate=candidate.model_dump(),
                confirmation_id=str(uuid.uuid4()),
                final_ticket=ticket.model_dump(),
                updated_at=_now(),
            )
            count = conn.execute(
                update(drafts)
                .where(
                    drafts.c.id == draft_id,
                    drafts.c.revision == revision,
                    drafts.c.state.in_(["draft", "confirmed"]),
                )
                .values(**changes)
            ).rowcount
            if count != 1:
                raise DeliveryError("Draft changed or is already prepared; reload before confirming")
            record.update(changes)
            self._event(conn, record, actor, "confirmed")
        return self._view(record)

    def prepare(self, confirmation_id: str, actor: str, profile_name: str, profile: ConnectionProfile) -> dict:
        """Freeze the confirmed revision and create its delivery in one DB transaction."""
        with self.engine.begin() as conn:
            found = conn.execute(select(drafts.c.id).where(drafts.c.confirmation_id == confirmation_id)).first()
            if found is None:
                raise DeliveryError("A current persisted confirmation is required")
            record = self._read(conn, found.id, actor, profile_name, profile)
            if record["state"] == "prepared":
                delivery = conn.execute(
                    select(deliveries).where(deliveries.c.digest == record["prepared_digest"])
                ).first()
                if delivery is None:
                    raise DeliveryError("Prepared delivery is missing; operator reconciliation required")
                return dict(delivery._mapping)
            change = prepare_change(
                Ticket.model_validate(record["final_ticket"]),
                actor,
                profile.audience,
                confirmation_id=confirmation_id,
            )
            changes = dict(
                state="prepared", revision=record["revision"] + 1, prepared_digest=change["digest"], updated_at=_now()
            )
            count = conn.execute(
                update(drafts)
                .where(
                    drafts.c.id == record["id"],
                    drafts.c.revision == record["revision"],
                    drafts.c.confirmation_id == confirmation_id,
                    drafts.c.state == "confirmed",
                )
                .values(**changes)
            ).rowcount
            if count != 1:
                raise DeliveryError("Confirmation changed; reload before preparing")
            delivery = Journal.prepare_in_transaction(conn, change)
            record.update(changes)
            self._event(conn, record, actor, "prepared")
            return delivery

    def events(self, draft_id: str, actor: str, profile_name: str, profile: ConnectionProfile) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            self._read(conn, draft_id, actor, profile_name, profile)
            rows = conn.execute(
                select(intake_events)
                .where(intake_events.c.draft_id == draft_id)
                .order_by(intake_events.c.revision, intake_events.c.recorded_at)
            )
            return [dict(row._mapping) for row in rows]


def prepare_flow_input(
    journal: Journal,
    value: dict,
    actor: str,
    profile_name: str,
    profile: ConnectionProfile,
    *,
    allow_legacy: bool = False,
) -> dict:
    """The shared server-side gate used by the actual component and local demo."""
    confirmation = value.get("confirmation_id")
    if confirmation:
        return CandidateStore(journal.engine).prepare(confirmation, actor, profile_name, profile)
    if allow_legacy:
        ticket = Ticket.model_validate(value)
        profile.authorize(actor, ticket.tenant)
        return journal.prepare(prepare_change(ticket, actor, profile.audience))
    raise DeliveryError("Confirm the current intake revision before preparing (see v0.2 migration notes)")
