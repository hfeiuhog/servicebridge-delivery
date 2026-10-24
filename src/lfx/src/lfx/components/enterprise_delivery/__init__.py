"""Enterprise delivery components registered in the Langflow catalog."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.enterprise_delivery.nodes import (
        EnterpriseConfirmTicket,
        EnterprisePrepareChange,
        EnterpriseServiceNowWriteback,
        EnterpriseTicketContract,
        EnterpriseTicketIntake,
    )

_dynamic_imports = {
    "EnterpriseConfirmTicket": "nodes",
    "EnterprisePrepareChange": "nodes",
    "EnterpriseServiceNowWriteback": "nodes",
    "EnterpriseTicketContract": "nodes",
    "EnterpriseTicketIntake": "nodes",
}
__all__ = [
    "EnterpriseConfirmTicket",
    "EnterprisePrepareChange",
    "EnterpriseServiceNowWriteback",
    "EnterpriseTicketContract",
    "EnterpriseTicketIntake",
]


def __getattr__(name: str) -> Any:
    if name not in _dynamic_imports:
        raise AttributeError(name)
    value = import_mod(name, _dynamic_imports[name], __spec__.parent)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return list(__all__)
