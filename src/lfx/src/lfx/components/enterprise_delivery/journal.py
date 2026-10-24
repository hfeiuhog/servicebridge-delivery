"""Durable writeback journal. PostgreSQL is the deployment target."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Column, MetaData, String, Table, UniqueConstraint, insert, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from lfx.components.enterprise_delivery.contracts import DeliveryError

metadata = MetaData()
deliveries = Table(
    "servicebridge_delivery",
    metadata,
    Column("digest", String(64), primary_key=True),
    Column("tenant", String(60), nullable=False),
    Column("case_ref", String(80), nullable=False),
    Column("state", String(20), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("receipt", JSON, nullable=True),
    UniqueConstraint("tenant", "case_ref", name="uq_servicebridge_tenant_case"),
)


class Journal:
    def __init__(self, engine: Engine):
        self.engine = engine

    def prepare(self, change: dict[str, Any]) -> dict[str, Any]:
        with self.engine.begin() as conn:
            return self.prepare_in_transaction(conn, change)

    @staticmethod
    def prepare_in_transaction(conn: Connection, change: dict[str, Any]) -> dict[str, Any]:
        """Allow confirmation freezing and preparation to share one transaction."""
        try:
            with conn.begin_nested():
                conn.execute(
                    insert(deliveries).values(
                        digest=change["digest"],
                        tenant=change["tenant"],
                        case_ref=change["case_ref"],
                        state="prepared",
                        payload=change,
                    )
                )
        except IntegrityError:
            row = conn.execute(
                select(deliveries).where(
                    deliveries.c.tenant == change["tenant"], deliveries.c.case_ref == change["case_ref"]
                )
            ).first()
            if row is None or row.digest != change["digest"]:
                raise DeliveryError(
                    "This customer case already has a different change; reconcile before revising"
                ) from None
        row = conn.execute(select(deliveries).where(deliveries.c.digest == change["digest"])).first()
        return dict(row._mapping)

    def get(self, digest: str) -> dict[str, Any]:
        with self.engine.connect() as conn:
            row = conn.execute(select(deliveries).where(deliveries.c.digest == digest)).first()
        if row is None:
            raise DeliveryError("The change was not prepared in the durable journal")
        return dict(row._mapping)

    def claim(self, digest: str) -> dict[str, Any]:
        with self.engine.begin() as conn:
            count = conn.execute(
                update(deliveries)
                .where(deliveries.c.digest == digest, deliveries.c.state == "prepared")
                .values(state="sending")
            ).rowcount
            row = conn.execute(select(deliveries).where(deliveries.c.digest == digest)).first()
        if row is None:
            raise DeliveryError("The change is not prepared")
        if count != 1 and row.state != "completed":
            raise DeliveryError("Writeback is in flight, failed or uncertain; human reconciliation is required")
        return dict(row._mapping)

    def finish(self, digest: str, state: str, receipt: dict[str, Any] | None = None) -> None:
        if state not in {"prepared", "completed", "failed", "uncertain"}:
            raise DeliveryError("Invalid delivery state")
        with self.engine.begin() as conn:
            count = conn.execute(
                update(deliveries)
                .where(deliveries.c.digest == digest, deliveries.c.state == "sending")
                .values(state=state, receipt=receipt)
            ).rowcount
            if count != 1:
                raise DeliveryError("The journal state changed during writeback")
