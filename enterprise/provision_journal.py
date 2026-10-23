"""Explicit database provisioning step for an operator-approved deployment."""

import os
from sqlalchemy import create_engine
from lfx.components.enterprise_delivery.journal import metadata

url = os.environ["SERVICEBRIDGE_DATABASE_URL"]
if not url.startswith("postgresql"):
    raise ValueError("Provision PostgreSQL before enabling writeback")
metadata.create_all(create_engine(url))
