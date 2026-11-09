"""The club's registration settings (one row) and who may change them."""

from members.services import access
from registration.models import RegistrationSettings

FIELDS = ("is_open", "intro_text", "terms_text", "payment_instructions", "invoice_due_days")


def get_settings() -> RegistrationSettings:
    obj = RegistrationSettings.objects.first()
    return obj or RegistrationSettings.objects.create()


def update_settings(actor, **values) -> RegistrationSettings:
    access.require_admin(actor)
    from members.services.errors import Invalid

    obj = get_settings()
    if "invoice_due_days" in values and values["invoice_due_days"] is not None and not 0 <= values["invoice_due_days"] <= 365:
        raise Invalid("Check the settings.", {"invoice_due_days": ["Between 0 and 365."]})
    for name in FIELDS:
        if values.get(name) is not None:
            setattr(obj, name, values[name].strip() if isinstance(values[name], str) else values[name])
    obj.save()
    return obj
