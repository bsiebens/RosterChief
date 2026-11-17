"""The club's registration settings (one row) and who may change them."""

from members.services import access
from members.services.errors import Invalid
from registration.models import RegistrationSettings

FIELDS = ("is_open", "intro_text", "terms_text", "payment_instructions", "invoice_due_days", "installment_offsets", "installment_grace_days", "installment_block_kinds")


def get_settings() -> RegistrationSettings:
    obj = RegistrationSettings.objects.first()
    return obj or RegistrationSettings.objects.create()


def valid_offsets(offsets) -> bool:
    """Days after the first due date for each later instalment: up to 11, ascending, 1 to 365 (an empty list means paying in one go)."""
    return len(offsets) <= 11 and sorted(set(offsets)) == list(offsets) and all(isinstance(days, int) and 1 <= days <= 365 for days in offsets)


def update_settings(actor, **values) -> RegistrationSettings:
    access.require_admin(actor)
    from events.models import Event

    obj = get_settings()
    errors = {}
    for field in ("invoice_due_days", "installment_grace_days"):
        days = values.get(field)
        if days is not None and not 0 <= days <= 365:
            errors[field] = ["Between 0 and 365."]
    offsets = values.get("installment_offsets")
    if offsets is not None and not valid_offsets(offsets):
        errors["installment_offsets"] = ["Enter up to 11 distinct day offsets in ascending order, from 1 to 365."]
    kinds = values.get("installment_block_kinds")
    if kinds is not None and (len(kinds) != len(set(kinds)) or any(kind not in Event.EventKind.values for kind in kinds)):
        errors["installment_block_kinds"] = ["Choose valid event types."]
    if errors:
        raise Invalid("Check the settings.", errors)
    for name in FIELDS:
        if values.get(name) is not None:
            setattr(obj, name, values[name].strip() if isinstance(values[name], str) else values[name])
    obj.save()
    return obj
