"""Helpers for interpreting a Field's ``options``: a JSON list of plain strings or of ``{"value", "label"}`` objects."""


def field_choices(field) -> list[tuple[str, str]]:
    """``[(value, label), ...]`` for a choice-type field (values and labels as strings)."""
    choices = []
    for option in field.options or []:
        if isinstance(option, dict):
            value = option.get("value")
            label = option.get("label") or value
        else:
            value = label = option
        if value is None:
            continue
        choices.append((str(value), str(label)))
    return choices


def allowed_values(field) -> set[str]:
    return {value for value, _label in field_choices(field)}
