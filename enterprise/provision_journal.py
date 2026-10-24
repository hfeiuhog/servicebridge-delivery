"""Explicit database provisioning step for an operator-approved deployment."""

import os

from lfx.components.enterprise_delivery import candidate_store
from lfx.components.enterprise_delivery.journal import metadata
from sqlalchemy import create_engine

url = os.environ["SERVICEBRIDGE_DATABASE_URL"]
if not url.startswith("postgresql"):
    raise ValueError("Provision PostgreSQL before enabling writeback")
metadata.create_all(create_engine(url), tables=[*candidate_store.metadata.tables.values()])
