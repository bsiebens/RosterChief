"""Management rules for forms, their fields and their sends. Every write starts with ``require_manage_forms``; broken rules
raise ``Invalid`` (422), refused deletes raise ``Conflict`` (409, ``code`` ``has_responses``)."""

import re

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.text import slugify

from events.models import Season
from formbuilder.models import Field, Form, FormSend, Submission
from members.models import Group, Member
from members.services import access
from members.services.errors import Invalid, ServiceError
from teams.models import Team

CHOICE_TYPES = (Field.FieldType.CHOICE, Field.FieldType.MULTICHOICE)
FORM_FIELDS = ("title", "description", "login_required", "is_active")
SEND_STATUSES = ("open", "closed", "all", "scheduled", "inactive")


class Conflict(ServiceError):
    """The change clashes with existing data (maps to 409). ``code`` is machine-readable."""

    def __init__(self, message: str, code: str = "conflict"):
        super().__init__(message)
        self.code = code


# --- forms ---------------------------------------------------------------------------------------------------------


def forms_queryset():
    """Forms with ``field_count`` (active fields), ``send_count`` and ``response_count`` annotated."""
    return Form.objects.annotate(
        field_count=Count("fields", filter=Q(fields__is_active=True), distinct=True),
        send_count=Count("sends", distinct=True),
        response_count=Count("sends__submissions", distinct=True),
    ).order_by("title")


def get_form(form_id) -> Form | None:
    return forms_queryset().filter(pk=form_id).first()


def _check_form(values: dict) -> None:
    if "title" in values and not (values["title"] or "").strip():
        raise Invalid("Check the form.", {"title": ["This field is required."]})


@transaction.atomic
def create_form(actor, **fields) -> Form:
    access.require_manage_forms(actor)
    values = {k: fields[k] for k in FORM_FIELDS if fields.get(k) is not None}
    values.setdefault("title", "")
    _check_form(values)
    values["title"] = values["title"].strip()
    return Form.objects.create(**values)


@transaction.atomic
def update_form(actor, form: Form, **fields) -> Form:
    access.require_manage_forms(actor)
    values = {k: fields[k] for k in FORM_FIELDS if fields.get(k) is not None}
    _check_form(values)
    for name, value in values.items():
        setattr(form, name, value.strip() if name == "title" else value)
    if values:
        form.save(update_fields=[*values, "modified"])
    return form


@transaction.atomic
def delete_form(actor, form: Form) -> None:
    access.require_manage_forms(actor)
    if Submission.objects.filter(send__form=form).exists():
        raise Conflict("This form has responses and cannot be deleted. Deactivate it instead.", "has_responses")
    form.delete()


@transaction.atomic
def duplicate_form(actor, form: Form, *, title: str | None = None) -> Form:
    """A copy with all fields (including inactive ones, in order) and no sends."""
    access.require_manage_forms(actor)
    title = (title or "").strip() or f"{form.title} (copy)"[:255]
    copy = Form.objects.create(title=title, description=form.description, login_required=form.login_required, is_active=True)
    for source in form.fields.order_by("order"):
        Field.objects.create(
            form=copy, key=source.key, label=source.label, field_type=source.field_type, required=source.required, help_text=source.help_text,
            section=source.section, order=source.order, is_active=source.is_active, options=source.options,
        )  # fmt: skip
    return copy


# --- fields --------------------------------------------------------------------------------------------------------


def normalize_options(raw, *, where: str, errors: dict, field_type: str):
    """Options as stored: strings stay strings, objects become ``{"value", "label"}``; ``None`` for a non-choice type."""
    if field_type not in CHOICE_TYPES:
        if raw:
            errors[f"{where}.options"] = ["Only choice fields have options."]
        return None
    options, seen = [], set()
    for option in raw or []:
        if isinstance(option, dict):
            value = str(option.get("value") if option.get("value") is not None else "").strip()
            label = str(option.get("label") or value).strip()
            normalized = {"value": value, "label": label}
        else:
            value = str(option).strip()
            normalized = value
        if not value:
            errors[f"{where}.options"] = ["Options cannot be empty."]
            return None
        if value in seen:
            errors[f"{where}.options"] = [f"Duplicate option value: {value}."]
            return None
        seen.add(value)
        options.append(normalized)
    if not options:
        errors[f"{where}.options"] = ["Add at least one option."]
    return options


_KEY_RE = re.compile(r"^[a-z0-9_-]{1,100}$")


@transaction.atomic
def replace_fields(actor, form: Form, items: list[dict]) -> Form:
    """Replace the whole ordered field list of ``form``. ``items`` are dicts with ``label``, ``field_type``, ``required``,
    ``help_text``, ``section``, ``is_active``, ``options`` and optionally ``id`` (update that field) and ``key`` (only
    used for a new field; a field's key never changes). Order is the list position. Fields left out are deleted, or --
    when they have answers -- kept and deactivated after the listed ones."""
    access.require_manage_forms(actor)
    existing = {f.pk: f for f in form.fields.select_for_update()}
    errors, prepared, seen_ids = {}, [], set()
    for index, item in enumerate(items):
        where = f"fields.{index}"
        label = (item.get("label") or "").strip()
        if not label:
            errors[f"{where}.label"] = ["This field is required."]
        field_type = item.get("field_type")
        if field_type not in Field.FieldType.values:
            errors[f"{where}.field_type"] = ["Unknown field type."]
            continue
        current = None
        if item.get("id") is not None:
            current = existing.get(item["id"])
            if current is None:
                errors[f"{where}.id"] = ["Unknown field."]
                continue
            if current.pk in seen_ids:
                errors[f"{where}.id"] = ["Listed twice."]
                continue
            seen_ids.add(current.pk)
            if field_type != current.field_type and current.answers.exists():
                errors[f"{where}.field_type"] = ["The type cannot change once the field has answers."]
        key = (item.get("key") or "").strip()
        if current is None and key and not _KEY_RE.match(key):
            errors[f"{where}.key"] = ["Use lowercase letters, digits, dashes and underscores."]
        options = normalize_options(item.get("options"), where=where, errors=errors, field_type=field_type)
        values = {
            "label": label, "field_type": field_type, "required": bool(item.get("required", True)), "help_text": item.get("help_text") or "",
            "section": (item.get("section") or "").strip(), "is_active": bool(item.get("is_active", True)), "options": options,
        }  # fmt: skip
        prepared.append((current, values, key))
    if errors:
        raise Invalid("Check the fields.", errors)

    used_keys = {f.key for f in existing.values()}
    for current, _values, key in prepared:
        if current is None and key and key in used_keys:
            errors["fields"] = [f"Duplicate key: {key}."]
        elif current is None and key:
            used_keys.add(key)
    if errors:
        raise Invalid("Check the fields.", errors)

    kept = set(seen_ids)
    for index, (current, values, key) in enumerate(prepared):
        if current is None:
            current = Field(form=form, key=key)
            if not key:  # generated from the label, unique among the form's existing and new keys
                base = slugify(values["label"])[:100] or "field"
                candidate, n = base, 2
                while candidate in used_keys:
                    candidate, n = f"{base[: 100 - len(str(n)) - 1]}-{n}", n + 1
                current.key = candidate
                used_keys.add(candidate)
        for name, value in values.items():
            setattr(current, name, value)
        current.order = index
        current.save()
        kept.add(current.pk)

    next_order = len(prepared)
    for field_id, leftover in existing.items():
        if field_id in kept:
            continue
        if leftover.answers.exists():
            leftover.is_active, leftover.order = False, next_order
            leftover.save(update_fields=["is_active", "order", "modified"])
            next_order += 1
        else:
            leftover.delete()
    return form


# --- sends ---------------------------------------------------------------------------------------------------------


def sends_queryset():
    return FormSend.objects.select_related("form", "created_by", "season").prefetch_related("teams", "groups", "invited_members", "excluded_members").annotate(response_count=Count("submissions", distinct=True))


def get_send(send_id) -> FormSend | None:
    return sends_queryset().filter(pk=send_id).first()


def filter_sends_by_status(qs, status: str | None, now=None):
    now = now or timezone.now()
    not_closed = Q(closes_at__isnull=True) | Q(closes_at__gte=now)
    if status == "open":  # still collecting or about to: switched on and not past its close
        return qs.filter(not_closed, is_active=True)
    if status == "closed":  # no longer collecting: past its close or switched off
        return qs.filter(Q(is_active=False) | Q(closes_at__lt=now))
    if status == "scheduled":
        return qs.filter(not_closed, is_active=True, opens_at__gt=now)
    if status == "inactive":
        return qs.filter(is_active=False)
    return qs


def _ids(model, ids, label: str) -> list:
    ids = list(dict.fromkeys(ids or []))
    found = list(model.objects.filter(pk__in=ids))
    if len(found) != len(ids):
        raise Invalid(f"Unknown {label}.", {f"{label}_ids": [f"Unknown {label}."]})
    return found


def _validate_send(form, state: dict) -> None:
    errors = {}
    if not form.is_active:
        errors["form"] = ["This form is not active."]
    if state["club_wide"] and (state["teams"] or state["groups"]):
        errors["club_wide"] = ["A whole-club send cannot also have teams or groups."]
    if not (state["club_wide"] or state["teams"] or state["groups"] or state["invited"]) and not state["is_public"]:
        errors["team_ids"] = ["Choose who to send this to."]
    if state["opens_at"] and state["closes_at"] and state["closes_at"] <= state["opens_at"]:
        errors["closes_at"] = ["Must be after the opening time."]
    if state["is_public"] and form.login_required:
        errors["is_public"] = ["A form that requires a login cannot have a public link."]
    if state["max_submissions_per_user"] is not None and state["max_submissions_per_user"] < 1:
        errors["max_submissions_per_user"] = ["Must be at least 1."]
    if errors:
        raise Invalid("Check the send.", errors)


def _aware(value):
    return timezone.make_aware(value) if value is not None and timezone.is_naive(value) else value


def _dispatch_notification(send_id) -> None:
    from formbuilder.services import notifications

    transaction.on_commit(lambda: notifications.dispatch_send_notification(send_id))


@transaction.atomic
def create_send(actor, form: Form, *, team_ids=(), group_ids=(), club_wide=False, invited_member_ids=(), excluded_member_ids=(), season_id=None, opens_at=None, closes_at=None, max_submissions_per_user=None, is_public=False, notify=True) -> FormSend:
    access.require_manage_forms(actor)
    teams, groups = _ids(Team, team_ids, "team"), _ids(Group, group_ids, "group")
    invited, excluded = _ids(Member, invited_member_ids, "invited_member"), _ids(Member, excluded_member_ids, "excluded_member")
    season = None
    if season_id is not None:
        season = Season.objects.filter(pk=season_id).first()
        if season is None:
            raise Invalid("Unknown season.", {"season_id": ["Unknown season."]})
    opens_at, closes_at = _aware(opens_at), _aware(closes_at)
    _validate_send(form, {"club_wide": club_wide, "teams": teams, "groups": groups, "invited": invited, "is_public": is_public, "opens_at": opens_at, "closes_at": closes_at, "max_submissions_per_user": max_submissions_per_user})
    send = FormSend.objects.create(form=form, club_wide=club_wide, season=season, opens_at=opens_at, closes_at=closes_at, max_submissions_per_user=max_submissions_per_user, is_public=is_public, created_by=Member.objects.filter(user=actor).first(),
        notified_at=None if notify else timezone.now())  # notify=False: mark as handled so the opening sweep never announces it
    send.teams.set(teams)
    send.groups.set(groups)
    send.invited_members.set(invited)
    send.excluded_members.set(excluded)
    if notify:
        _dispatch_notification(send.pk)
    return send


_SEND_SCALARS = ("club_wide", "opens_at", "closes_at", "max_submissions_per_user", "is_active", "is_public")


@transaction.atomic
def update_send(actor, send: FormSend, *, notify=False, **fields) -> FormSend:
    """Partial update (keys present in ``fields`` change; ``max_submissions_per_user`` / ``opens_at`` / ``closes_at`` /
    ``season_id`` may be set to ``None``). ``notify`` dispatches the notification when this switches the send on."""
    access.require_manage_forms(actor)
    was_active = send.is_active
    teams = _ids(Team, fields["team_ids"], "team") if "team_ids" in fields else list(send.teams.all())
    groups = _ids(Group, fields["group_ids"], "group") if "group_ids" in fields else list(send.groups.all())
    invited = _ids(Member, fields["invited_member_ids"], "invited_member") if "invited_member_ids" in fields else list(send.invited_members.all())
    excluded = _ids(Member, fields["excluded_member_ids"], "excluded_member") if "excluded_member_ids" in fields else None
    scalars = {name: fields[name] for name in _SEND_SCALARS if name in fields}
    for name in ("opens_at", "closes_at"):
        if name in scalars:
            scalars[name] = _aware(scalars[name])
    for name in ("club_wide", "is_active", "is_public"):
        if name in scalars and scalars[name] is None:
            del scalars[name]
    season = send.season
    if "season_id" in fields:
        season = Season.objects.filter(pk=fields["season_id"]).first() if fields["season_id"] is not None else None
        if fields["season_id"] is not None and season is None:
            raise Invalid("Unknown season.", {"season_id": ["Unknown season."]})
    state = {"club_wide": send.club_wide, "opens_at": send.opens_at, "closes_at": send.closes_at, "max_submissions_per_user": send.max_submissions_per_user, "is_public": send.is_public, **scalars}
    if _changes_rules(fields):
        _validate_send(send.form, {**state, "teams": teams, "groups": groups, "invited": invited})
    for name, value in scalars.items():
        setattr(send, name, value)
    send.season = season
    send.save()
    if "team_ids" in fields:
        send.teams.set(teams)
    if "group_ids" in fields:
        send.groups.set(groups)
    if "invited_member_ids" in fields:
        send.invited_members.set(invited)
    if excluded is not None:
        send.excluded_members.set(excluded)
    if notify and send.is_active and not was_active:
        _dispatch_notification(send.pk)
    return send


def _changes_rules(fields: dict) -> bool:
    """Whether the update touches anything the create rules check (a switch-off or a rename-free change stays allowed even
    when an older send would no longer pass, e.g. its form was deactivated since)."""
    return any(name in fields for name in ("team_ids", "group_ids", "club_wide", "invited_member_ids", "opens_at", "closes_at", "is_public", "max_submissions_per_user"))


@transaction.atomic
def delete_send(actor, send: FormSend) -> None:
    access.require_manage_forms(actor)
    if send.submissions.exists():
        raise Conflict("This send has responses and cannot be deleted. Close it instead.", "has_responses")
    send.delete()


@transaction.atomic
def close_send(actor, send: FormSend) -> FormSend:
    access.require_manage_forms(actor)
    now = timezone.now()
    send.closes_at = now
    if send.opens_at is not None and send.opens_at > now:
        send.opens_at = None
    send.save(update_fields=["closes_at", "opens_at", "modified"])
    return send


@transaction.atomic
def reopen_send(actor, send: FormSend) -> FormSend:
    access.require_manage_forms(actor)
    send.closes_at, send.is_active = None, True
    send.save(update_fields=["closes_at", "is_active", "modified"])
    return send


@transaction.atomic
def delete_submission(actor, submission: Submission) -> None:
    access.require_manage_forms(actor)
    submission.delete()  # a post_delete handler removes the stored files


def open_answer_file(actor, answer):
    """The answer's stored file opened for reading in binary mode (the caller closes it; ``FileResponse`` does). Raises
    ``FileNotFoundError`` when there is none."""
    access.require_manage_forms(actor)
    if not answer.file or not answer.file.storage.exists(answer.file.name):
        raise FileNotFoundError(answer.file.name or "")
    return answer.file.storage.open(answer.file.name, "rb")
