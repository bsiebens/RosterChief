"""Push configuration: read, validate and update the editable ``PushSettings`` row. Credentials stay in the environment (status only)."""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email

from notifications.models import PushSettings

PROVIDERS = {"apns": "APNs (iOS)", "fcm": "FCM (Android)", "webpush": "Web push"}
ENABLED_FIELD = {"apns": "apns_enabled", "fcm": "fcm_enabled", "webpush": "webpush_enabled"}

BOOLS = ("apns_enabled", "fcm_enabled", "webpush_enabled")
INT_RANGES = {
    "outbox_max_attempts": (1, 20),
    "sent_retention_days": (1, 3650),
    "failed_retention_days": (1, 3650),
    "stale_device_days": (1, 3650),
    "disabled_device_retention_days": (1, 3650),
    "unseen_device_retention_days": (1, 3650),
    "health_min_requests": (0, 10000),
}
PERCENTS = ("degraded_below_percent", "down_below_percent")
FIELDS = (*BOOLS, "retry_delays_minutes", *INT_RANGES, *PERCENTS, "alert_emails")


def settings_dict(row: PushSettings | None = None) -> dict:
    row = row or PushSettings.get()
    return {name: list(getattr(row, name)) if name == "retry_delays_minutes" else getattr(row, name) for name in FIELDS}


def providers_status(row: PushSettings | None = None) -> list[dict]:
    from notifications import gateways

    row = row or PushSettings.get()
    out = []
    for provider, label in PROVIDERS.items():
        status = gateways.credentials_status(provider)
        out.append({"provider": provider, "label": label, "configured": bool(status.get("configured")), "missing": list(status.get("missing", [])), "enabled": getattr(row, ENABLED_FIELD[provider])})
    return out


def parse_emails(value) -> tuple[str, str | None]:
    """Normalise a comma/semicolon/newline separated string (or a list) to ``a@x.com, b@y.com``. Returns (value, error)."""
    if value is None:
        return "", None
    parts = value if isinstance(value, list) else str(value).replace(";", ",").replace("\n", ",").split(",")
    emails, seen = [], set()
    for part in parts:
        email = str(part).strip()
        if not email:
            continue
        try:
            validate_email(email)
        except DjangoValidationError:
            return "", f"'{email}' is not a valid email address."
        if email.lower() not in seen:
            seen.add(email.lower())
            emails.append(email)
    return ", ".join(emails), None


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(changes: dict, current: PushSettings) -> tuple[dict, dict[str, list[str]]]:
    """Check a partial update. Returns (cleaned values, errors by field)."""
    clean: dict = {}
    errors: dict[str, list[str]] = {}

    def fail(field, message):
        errors.setdefault(field, []).append(message)

    for name in BOOLS:
        if name in changes:
            if isinstance(changes[name], bool):
                clean[name] = changes[name]
            else:
                fail(name, "Must be true or false.")
    if "retry_delays_minutes" in changes:
        delays = changes["retry_delays_minutes"]
        if not isinstance(delays, list) or not (1 <= len(delays) <= 10):
            fail("retry_delays_minutes", "Provide between 1 and 10 delays.")
        elif not all(_is_int(d) and 1 <= d <= 1440 for d in delays):
            fail("retry_delays_minutes", "Every delay must be a whole number of minutes between 1 and 1440.")
        else:
            clean["retry_delays_minutes"] = list(delays)
    for name, (low, high) in INT_RANGES.items():
        if name in changes:
            v = changes[name]
            if _is_int(v) and low <= v <= high:
                clean[name] = v
            else:
                fail(name, f"Must be a whole number between {low} and {high}.")
    for name in PERCENTS:
        if name in changes:
            v = changes[name]
            if _is_int(v) and 0 <= v <= 100:
                clean[name] = v
            else:
                fail(name, "Must be a whole number between 0 and 100.")
    if "alert_emails" in changes:
        value, error = parse_emails(changes["alert_emails"])
        if error:
            fail("alert_emails", error)
        else:
            clean["alert_emails"] = value
    down = clean.get("down_below_percent", current.down_below_percent)
    degraded = clean.get("degraded_below_percent", current.degraded_below_percent)
    if not any(n in errors for n in PERCENTS) and down >= degraded:
        fail("down_below_percent", "Down must be below degraded.")
    return clean, errors


def update(changes: dict) -> tuple[PushSettings, list[str], dict[str, list[str]]]:
    """Apply a partial update. Returns (row, names of fields that really changed, errors). Nothing is saved when there are errors."""
    row = PushSettings.get()
    clean, errors = validate(changes, row)
    if errors:
        return row, [], errors
    changed = [name for name, value in clean.items() if getattr(row, name) != value]
    if changed:
        for name in changed:
            setattr(row, name, clean[name])
        row.save(update_fields=changed)
    return row, changed, {}
