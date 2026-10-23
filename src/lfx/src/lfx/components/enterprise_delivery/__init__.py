"""Enterprise delivery components registered in the Langflow catalog."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.enterprise_delivery.nodes import (
        EnterprisePrepareChange,
        EnterpriseServiceNowWriteback,
        EnterpriseTicketContract,
    )

_dynamic_imports = {
    "EnterprisePrepareChange": "nodes",
    "EnterpriseServiceNowWriteback": "nodes",
    "EnterpriseTicketContract": "nodes",
}
__all__ = ["EnterprisePrepareChange", "EnterpriseServiceNowWriteback", "EnterpriseTicketContract"]


def __getattr__(name: str) -> Any:
    if name not in _dynamic_imports:
        raise AttributeError(name)
    value = import_mod(name, _dynamic_imports[name], __spec__.parent)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return list(__all__)
