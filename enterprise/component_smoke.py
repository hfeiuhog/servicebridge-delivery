from lfx.custom.custom_component.component import Component

from lfx import components

category = components.enterprise_delivery
names = [
    "EnterpriseTicketContract",
    "EnterpriseTicketIntake",
    "EnterpriseConfirmTicket",
    "EnterprisePrepareChange",
    "EnterpriseServiceNowWriteback",
]
for name in names:
    cls = getattr(category, name)
    assert issubclass(cls, Component)
    instance = cls()
    assert instance.name == name
    assert len(instance.outputs) == 1
    assert callable(getattr(instance, instance.outputs[0].method))
print("Five real Langflow component classes loaded and instantiated through platform catalog")
