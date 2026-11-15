"""The line-up template of a sport: ordered groups of slots, e.g. ``[{"key": "line1", "label": "Line 1", "slots": [{"key": "c", "label": "C"}]}]``.

A player's slot is stored as ``"<group key>.<slot key>"``. Platform admins edit the template (``clean_template`` validates it);
everything that reads one is tolerant: unknown slots are simply "unassigned". Slots are informational only and never used for statistics."""

import re

KEY_RE = re.compile(r"^[a-z0-9_-]{1,30}$")
MAX_GROUPS, MAX_SLOTS_PER_GROUP, MAX_SLOTS, MAX_LABEL = 12, 12, 60, 40


class TemplateError(ValueError):
    pass


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:30] or "x"


def _unique(base: str, used: set) -> str:
    key, n = base, 2
    while key in used:
        key = f"{base[:26]}-{n}"
        n += 1
    return key


def clean_template(raw) -> list[dict]:
    """Validated, normalised copy of ``raw`` (labels stripped; a blank key is generated from the label). Raises ``TemplateError``."""
    if not isinstance(raw, list):
        raise TemplateError("The template must be a list of groups.")
    if len(raw) > MAX_GROUPS:
        raise TemplateError(f"At most {MAX_GROUPS} groups.")
    out, group_keys, total = [], set(), 0
    for g in raw:
        if not isinstance(g, dict):
            raise TemplateError("Every group needs a label and slots.")
        label = str(g.get("label") or "").strip()
        if not label:
            raise TemplateError("Every group needs a label.")
        if len(label) > MAX_LABEL:
            raise TemplateError(f"Labels are at most {MAX_LABEL} characters.")
        key = str(g.get("key") or "").strip() or _unique(_slug(label), group_keys)
        if not KEY_RE.match(key):
            raise TemplateError(f"Invalid group key: {key}.")
        if key in group_keys:
            raise TemplateError(f"Duplicate group key: {key}.")
        group_keys.add(key)
        slots_raw = g.get("slots") or []
        if not isinstance(slots_raw, list) or not slots_raw:
            raise TemplateError(f"Group “{label}” needs at least one slot.")
        if len(slots_raw) > MAX_SLOTS_PER_GROUP:
            raise TemplateError(f"At most {MAX_SLOTS_PER_GROUP} slots per group.")
        slot_keys, slots = set(), []
        for s in slots_raw:
            if not isinstance(s, dict):
                raise TemplateError("Every slot needs a label.")
            slabel = str(s.get("label") or "").strip()
            if not slabel:
                raise TemplateError("Every slot needs a label.")
            if len(slabel) > MAX_LABEL:
                raise TemplateError(f"Labels are at most {MAX_LABEL} characters.")
            skey = str(s.get("key") or "").strip() or _unique(_slug(slabel), slot_keys)
            if not KEY_RE.match(skey):
                raise TemplateError(f"Invalid slot key: {skey}.")
            if skey in slot_keys:
                raise TemplateError(f"Duplicate slot key “{skey}” in “{label}”.")
            slot_keys.add(skey)
            slots.append({"key": skey, "label": slabel})
        total += len(slots)
        out.append({"key": key, "label": label, "slots": slots})
    if total > MAX_SLOTS:
        raise TemplateError(f"At most {MAX_SLOTS} slots in total.")
    return out


def slot_keys(template) -> set[str]:
    """Every valid ``"<group>.<slot>"`` of a (stored) template; tolerant of malformed data."""
    keys = set()
    for g in template if isinstance(template, list) else []:
        if isinstance(g, dict):
            for s in g.get("slots") or []:
                if isinstance(s, dict) and g.get("key") and s.get("key"):
                    keys.add(f"{g['key']}.{s['key']}")
    return keys
