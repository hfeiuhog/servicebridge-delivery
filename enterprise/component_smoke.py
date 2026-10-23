from lfx import components
from lfx.custom.custom_component.component import Component

category = components.enterprise_delivery
names = ["EnterpriseTicketContract", "EnterprisePrepareChange", "EnterpriseServiceNowWriteback"]
for name in names:
    cls = getattr(category, name)
    assert issubclass(cls, Component)
    instance = cls()
    assert instance.name == name
    assert len(instance.outputs) == 1
    assert callable(getattr(instance, instance.outputs[0].method))
print("Three real Langflow component classes loaded and instantiated through platform catalog")
