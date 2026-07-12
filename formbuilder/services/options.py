"""Helpers for interpreting a Field's ``options`` (choice definitions).

``options`` is a JSON list, either of plain strings (``["a", "b"]``) or of
``{"value": ..., "label": ...}`` dicts.
"""


def field_choices(field):
    """Return ``[(value, label), ...]`` for a choice-type field."""
    choices = []
    for option in field.options or []:
        if isinstance(option, dict):
            value = option.get("value")
            label = option.get("label", value)
        else:
            value = label = option
        choices.append((value, label))
    return choices


def allowed_values(field):
    """Return the set of accepted values for a choice-type field."""
    return {value for value, _ in field_choices(field)}
