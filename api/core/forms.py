"""Django-form bridge shared by the routers that reuse the web screens' forms."""

import datetime

from api.core.errors import ApiError

#: Form field name -> the JSON key every router already shares.
BASE_ALIASES = {"__all__": "non_field_errors"}


def form_error(form, aliases: dict | None = None) -> ApiError:
    """A failed form -> ``422 validation_error`` with ``fields`` named after the JSON keys.

    ``aliases`` renames form fields to API keys (e.g. ``{"family": "family_id"}``); the
    form-wide ``__all__`` key always becomes ``non_field_errors``."""
    names = {**BASE_ALIASES, **(aliases or {})}
    fields = {names.get(name, name): [str(message) for message in messages] for name, messages in form.errors.items()}
    return ApiError(422, "validation_error", fields=fields)


def post_data(values: dict, *, multiple: dict | None = None, single: dict | None = None, checkboxes=(), extra: dict | None = None) -> dict:
    """JSON values -> what a browser would have POSTed to a Django form.

    ``None`` is left out (the form reads it as blank). ``multiple`` maps a JSON key holding a
    list of ids to the form field name, ``single`` a key holding one id; ``checkboxes`` are
    booleans (sent as ``"on"`` only when true); datetimes become ISO strings. ``extra`` is merged
    first (e.g. ``{"teams": [...]}``)."""
    multiple, single = multiple or {}, single or {}
    data = dict(extra or {})
    for key, value in values.items():
        if value is None:
            continue
        if key in multiple:
            data[multiple[key]] = [str(pk) for pk in value]
        elif key in single:
            data[single[key]] = str(value)
        elif key in checkboxes:
            if value:
                data[key] = "on"
        elif isinstance(value, datetime.datetime):
            data[key] = value.isoformat()
        else:
            data[key] = value
    return data
