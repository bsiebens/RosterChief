"""Onboarding requirements and each membership's checklist.

A requirement is *resolved* when completed (and not expired) or bypassed. Resolving never touches ``fee_status`` or
``status``; activation is a separate explicit step (``memberships.activate``).

Kinds: a requirement is a *document* (received, optionally with a file) or a *question* (extra information, completed by
answering it; ``question_type`` text / textarea / number / date / choice / multichoice / yes_no). An answer is validated with the
same cleaning code the form builder uses, stored on the status row (``answer``) and completes the item; expiry, reminders,
bypass, reopen (which clears the answer) and event gating work the same for both kinds.

Expiry: a requirement may be valid for N months (``valid_for_months``); a completion then carries a ``valid_until`` date
(from the requirement, or set by staff). Once that date has passed the item reads as open again -- it blocks exactly like a
missing one. ``ChecklistItem.display`` is the finer label for screens: missing / complete / expiring_soon / expired /
bypassed (``state`` stays open / complete / bypassed)."""

import datetime
import os
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from dateutil.relativedelta import relativedelta
from django.core.files import File
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from events.models import Event
from events.services.attachments import ALLOWED_EXTENSIONS, MAX_ATTACHMENT_BYTES, display_name
from members.models import ClubMembership, MemberRequirementStatus, OnboardingRequirement

from . import audit
from .access import require_admin
from .errors import Invalid

REQUIREMENT_FIELDS = ("name", "description", "kind", "question_type", "options", "min_value", "max_value", "requires_document", "blocked_event_kinds", "is_active", "valid_for_months", "reminder_days_before")
MAX_VALID_MONTHS = 120
MAX_REMINDER_DAYS = 365
Kind = OnboardingRequirement.Kind
QuestionType = OnboardingRequirement.QuestionType
CHOICE_TYPES = (QuestionType.CHOICE, QuestionType.MULTICHOICE)
_TRUE = {"true", "yes", "y", "1", "on", "ja", "oui"}
_FALSE = {"false", "no", "n", "0", "off", "nee", "non"}


@dataclass
class ChecklistItem:
    requirement: OnboardingRequirement
    state: str  # "open" | "complete" | "bypassed"; an expired completion is "open"
    status: MemberRequirementStatus | None = None
    #: missing / pending / complete / expiring_soon / expired / bypassed
    display: str = "missing"

    @property
    def resolved(self) -> bool:
        return self.state != "open"

    @property
    def valid_until(self) -> datetime.date | None:
        return self.status.valid_until if self.status else None

    @property
    def answer(self) -> Any:
        """A question's current answer (``None`` while open or for a document)."""
        return self.status.answer if self.status else None

    @property
    def days_left(self) -> int | None:
        """Days until the completion expires (negative once it has), ``None`` when it has no expiry."""
        return (self.valid_until - timezone.localdate()).days if self.valid_until else None


def open_requirements() -> QuerySet[OnboardingRequirement]:
    """Requirements that apply to memberships right now (active ones), by name."""
    return OnboardingRequirement.objects.filter(is_active=True)


def _validated(values: dict[str, Any]) -> dict[str, Any]:
    unknown = set(values) - set(REQUIREMENT_FIELDS)
    if unknown:
        raise Invalid(f"Unknown fields: {sorted(unknown)}")
    if "name" in values:
        values["name"] = values["name"].strip()
        if not values["name"]:
            raise Invalid("A requirement needs a name.", {"name": ["Required."]})
    if "valid_for_months" in values and values["valid_for_months"] is not None:
        months = values["valid_for_months"]
        if not 1 <= months <= MAX_VALID_MONTHS:
            raise Invalid("Check the validity.", {"valid_for_months": [f"Use 1 to {MAX_VALID_MONTHS} months, or leave empty for no expiry."]})
    if "reminder_days_before" in values:
        days = values["reminder_days_before"]
        if days is None or not 0 <= days <= MAX_REMINDER_DAYS:
            raise Invalid("Check the reminder.", {"reminder_days_before": [f"Use 0 to {MAX_REMINDER_DAYS} days."]})
    if "kind" in values and values["kind"] not in Kind.values:
        raise Invalid("Check the kind.", {"kind": ["Invalid choice."]})
    if "question_type" in values and values["question_type"] not in ("", *QuestionType.values):
        raise Invalid("Check the question type.", {"question_type": ["Invalid choice."]})
    if "options" in values and values["options"] is not None:
        from formbuilder.services.management import normalize_options

        errors: dict = {}
        cleaned = normalize_options(values["options"], where="options", errors=errors, field_type=QuestionType.CHOICE)
        if errors:
            raise Invalid("Check the options.", {"options": next(iter(errors.values()))})
        values["options"] = cleaned
    for bound in ("min_value", "max_value"):
        if values.get(bound) is not None:
            try:
                values[bound] = Decimal(str(values[bound]))
            except ArithmeticError:
                raise Invalid("Check the range.", {bound: ["Enter a number."]}) from None
    if "blocked_event_kinds" in values:
        kinds = list(values["blocked_event_kinds"])
        bad = [k for k in kinds if k not in Event.EventKind.values]
        if bad:
            raise Invalid(f"Unknown event kinds: {bad}", {"blocked_event_kinds": ["Invalid choice."]})
        values["blocked_event_kinds"] = kinds
    return values


def _normalise_kind(current: dict, values: dict) -> dict:
    """Settle the fields that only make sense for one kind. ``current`` is the stored state (or defaults), ``values`` what is being set;
    the merged result is validated and the fields of the other kind are cleared."""
    merged = {**current, **values}
    kind = merged.get("kind") or Kind.DOCUMENT
    if kind == Kind.DOCUMENT:
        values.update({"question_type": "", "options": None, "min_value": None, "max_value": None})
        return values
    qtype = merged.get("question_type") or ""
    if qtype not in QuestionType.values:
        raise Invalid("Choose what kind of question this is.", {"question_type": ["Required."]})
    values["requires_document"] = False  # a question never has a file
    if qtype in CHOICE_TYPES:
        if not merged.get("options"):
            raise Invalid("A choice needs options.", {"options": ["Add at least one option."]})
    else:
        values["options"] = None
    if qtype == QuestionType.NUMBER:
        low, high = merged.get("min_value"), merged.get("max_value")
        if low is not None and high is not None and low > high:
            raise Invalid("Check the range.", {"max_value": ["Must not be lower than the minimum."]})
    else:
        values["min_value"] = values["max_value"] = None
    return values


def _name_free(name: str, exclude=None) -> None:
    qs = OnboardingRequirement.objects.filter(name__iexact=name)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    if qs.exists():
        raise Invalid("A requirement with this name already exists.", {"name": ["Already in use."]})


@transaction.atomic
def create_requirement(
    actor, *, name: str, description: str = "", requires_document: bool = False, blocked_event_kinds=(), is_active: bool = True, valid_for_months: int | None = None, reminder_days_before: int = 30,
    kind: str = Kind.DOCUMENT, question_type: str = "", options=None, min_value=None, max_value=None,
) -> OnboardingRequirement:  # fmt: skip
    """Define a requirement. ``blocked_event_kinds`` must be ``Event.EventKind`` values. A question needs ``question_type`` (and ``options`` for a choice)."""
    require_admin(actor)
    values = _validated(
        {
            "name": name, "description": description, "requires_document": requires_document, "blocked_event_kinds": blocked_event_kinds, "is_active": is_active,
            "valid_for_months": valid_for_months, "reminder_days_before": reminder_days_before, "kind": kind, "question_type": question_type, "options": options,
            "min_value": min_value, "max_value": max_value,
        }
    )  # fmt: skip
    values = _normalise_kind({}, values)
    _name_free(values["name"])
    requirement = OnboardingRequirement.objects.create(**values)
    audit.record(actor, "requirement.created", target=requirement, changes=audit.snapshot(requirement, REQUIREMENT_FIELDS))
    return requirement


@transaction.atomic
def update_requirement(actor, requirement: OnboardingRequirement, **values) -> OnboardingRequirement:
    require_admin(actor)
    values = _validated(values)
    if "name" in values:
        _name_free(values["name"], exclude=requirement)
    current = {f: getattr(requirement, f) for f in ("kind", "question_type", "options", "min_value", "max_value", "requires_document")}
    if {"kind", "question_type", "options", "min_value", "max_value", "requires_document"} & set(values):
        values = _normalise_kind(current, values)
        if values.get("kind", requirement.kind) != requirement.kind and requirement.statuses.exists():
            raise Invalid("A requirement in use cannot change kind. Add a new one instead.", {"kind": ["Already in use."]}, code="in_use")
        if values.get("question_type", requirement.question_type) != requirement.question_type and requirement.statuses.exclude(answer=None).exists():
            raise Invalid("This question already has answers, so its type cannot change.", {"question_type": ["Already answered."]}, code="in_use")
    changes = audit.diff(requirement, values)
    if changes:
        for k, v in values.items():
            setattr(requirement, k, v)
        requirement.save()
        audit.record(actor, "requirement.updated", target=requirement, changes=changes)
    return requirement


@transaction.atomic
def delete_requirement(actor, requirement: OnboardingRequirement) -> None:
    """Delete a requirement and every membership's status for it. Prefer ``is_active=False`` to keep history."""
    require_admin(actor)
    pk, changes, label = requirement.pk, audit.snapshot(requirement, REQUIREMENT_FIELDS, deleted=True), requirement.name
    requirement.delete()
    entry = audit.record(actor, "requirement.deleted", changes=changes)
    entry.target_type, entry.target_id, entry.target_label = "onboardingrequirement", str(pk), label[:255]
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])


def item_for(requirement: OnboardingRequirement, status: MemberRequirementStatus | None, today: datetime.date | None = None) -> ChecklistItem:
    """The checklist item for one requirement and its (possibly missing) status row."""
    today = today or timezone.localdate()
    if status and status.is_bypassed:
        return ChecklistItem(requirement, "bypassed", status, "bypassed")
    if status and status.is_pending_review:
        return ChecklistItem(requirement, "open", status, "pending")  # handed in, not approved yet: still open
    if status and status.is_complete:
        if status.valid_until is not None and status.valid_until < today:
            return ChecklistItem(requirement, "open", status, "expired")  # runs out: open again
        if status.valid_until is not None and (status.valid_until - today).days <= requirement.reminder_days_before:
            return ChecklistItem(requirement, "complete", status, "expiring_soon")
        return ChecklistItem(requirement, "complete", status, "complete")
    return ChecklistItem(requirement, "open", status, "missing")


def checklist_for(membership: ClubMembership, today: datetime.date | None = None) -> list[ChecklistItem]:
    """One item per active requirement, with its state for this membership."""
    statuses = {s.requirement_id: s for s in membership.requirement_statuses.all()}
    return [item_for(requirement, statuses.get(requirement.pk), today) for requirement in open_requirements()]


def unresolved(membership: ClubMembership) -> list[OnboardingRequirement]:
    """Active requirements this membership has neither completed nor bypassed."""
    return [i.requirement for i in checklist_for(membership) if not i.resolved]


def _status(membership: ClubMembership, requirement: OnboardingRequirement) -> MemberRequirementStatus:
    if not requirement.is_active:
        raise Invalid("This requirement is no longer active.")
    status, _ = MemberRequirementStatus.objects.get_or_create(membership=membership, requirement=requirement)
    return status


def check_document(document: File) -> None:
    """Documents and pictures only, not empty, at most ``MAX_ATTACHMENT_BYTES`` (same rules as event attachments)."""
    name = display_name(getattr(document, "name", ""))
    if not name or os.path.splitext(name)[1].lower() not in ALLOWED_EXTENSIONS:
        raise Invalid("This file type is not allowed.", {"document": ["This file type is not allowed."]})
    size = getattr(document, "size", 0) or 0
    if size <= 0:
        raise Invalid("The file is empty.", {"document": ["The submitted file is empty."]})
    if size > MAX_ATTACHMENT_BYTES:
        raise Invalid("The file is too large.", {"document": [f"The file is larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB."]})


def _answer_error(message: str) -> Invalid:
    return Invalid("Check the answer.", {"answer": [message]})


def validate_answer(requirement: OnboardingRequirement, raw: Any) -> Any:
    """The cleaned answer to a question (raises ``Invalid`` keyed ``answer``). Text, number, date and choices go through the form builder's
    own cleaning code (a number within the question's range, a choice among its options); yes/no becomes a boolean."""
    if requirement.kind != Kind.QUESTION:
        raise Invalid("This item is not a question.", {"requirement_id": ["Not a question."]}, code="not_a_question")
    qtype = requirement.question_type
    if qtype == QuestionType.YES_NO:
        if isinstance(raw, bool):
            return raw
        text = str(raw if raw is not None else "").strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise _answer_error("Answer yes or no.")
    from formbuilder.services.submission import _clean_value

    field = SimpleNamespace(field_type=str(qtype), required=True, options=requirement.options, min_value=requirement.min_value, max_value=requirement.max_value)
    try:
        value = _clean_value(field, raw, None)
    except ValueError as exc:
        raise _answer_error(str(exc)) from None
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")  # "4.5", "12" (never "1E+1")
    return value


def _expiry(requirement: OnboardingRequirement, on: datetime.date, valid_until: datetime.date | None) -> datetime.date | None:
    """The expiry to store: what staff entered, else ``valid_for_months`` after ``on``, else none."""
    if valid_until is not None:
        return valid_until
    if requirement.valid_for_months:
        return on + relativedelta(months=requirement.valid_for_months)
    return None


_UNSET: Any = object()


def _resolve(actor, membership, requirement, action: str, *, complete: bool, document: File | None, note: str, valid_until: datetime.date | None = None, answer: Any = _UNSET) -> MemberRequirementStatus:
    status = _status(membership, requirement)
    before = {"is_complete": status.is_complete, "is_bypassed": status.is_bypassed, "note": status.note, "document": status.document.name or "", "valid_until": str(status.valid_until or ""), "answer": status.answer}
    today = timezone.localdate()
    if complete and requirement.kind == Kind.QUESTION and answer is _UNSET:
        raise Invalid("Answer the question instead.", {"answer": ["Required."]}, code="use_answer")
    if answer is not _UNSET:
        status.answer = answer
    if complete:
        if requirement.requires_document and document is None and not status.document:
            raise Invalid("This requirement needs a document.", {"document": ["Required."]})
        if document is not None:
            check_document(document)
            status.document.save(getattr(document, "name", "document"), document, save=False)
        if valid_until is not None and valid_until < today:
            raise Invalid("The expiry date is in the past.", {"valid_until": ["Must be today or later."]})
        status.valid_until = _expiry(requirement, today, valid_until)
    else:
        status.valid_until = None  # not needed for this person: nothing to expire
        status.answer = None
    status.is_complete, status.is_bypassed = complete, not complete
    status.is_pending_review = False
    status.completed_at = timezone.now()
    status.completed_by = actor
    status.last_reminder_at, status.last_reminder_kind = None, ""  # a fresh completion starts a fresh reminder cycle
    if note:
        status.note = note
    status.save()
    after = {"is_complete": status.is_complete, "is_bypassed": status.is_bypassed, "note": status.note, "document": status.document.name or "", "valid_until": str(status.valid_until or ""), "answer": status.answer}
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    audit.record(actor, action, member=membership.member, target=status, changes=changes, target_label=f"{requirement.name} ({membership.season})")
    return status


@transaction.atomic
def mark_complete(actor, membership: ClubMembership, requirement: OnboardingRequirement, *, document: File | None = None, note: str = "", valid_until: datetime.date | None = None) -> MemberRequirementStatus:
    """Mark received. A requirement with ``requires_document`` needs ``document`` (or one already attached). ``valid_until``
    overrides the requirement's own validity; completing an item again (new file, new date) simply updates it."""
    require_admin(actor)
    return _resolve(actor, membership, requirement, "onboarding.completed", complete=True, document=document, note=note, valid_until=valid_until)


@transaction.atomic
def answer(actor, membership: ClubMembership, requirement: OnboardingRequirement, raw: Any, *, note: str = "", valid_until: datetime.date | None = None) -> MemberRequirementStatus:
    """Staff answer a question on someone's behalf (or correct the answer): validated for the question's type, it completes the item."""
    require_admin(actor)
    return answer_as(actor, membership, requirement, raw, note=note, valid_until=valid_until)


def answer_as(actor, membership: ClubMembership, requirement: OnboardingRequirement, raw: Any, *, note: str = "", valid_until: datetime.date | None = None) -> MemberRequirementStatus:
    """Record an answer without a permission check: ``actor`` is the staff user, or ``None`` for the registrant on the status page
    (whose own check is the batch token). Answering again replaces the answer and renews the expiry."""
    cleaned = validate_answer(requirement, raw)
    with transaction.atomic():
        return _resolve(actor, membership, requirement, "onboarding.answered", complete=True, document=None, note=note, valid_until=valid_until, answer=cleaned)


@transaction.atomic
def submit_as(actor, membership: ClubMembership, requirement: OnboardingRequirement, *, document: File | None = None, raw: Any = _UNSET, valid_until: datetime.date | None = None) -> MemberRequirementStatus:
    """A member (or their guardian) hands in a document or an answer. It waits for staff: the item stays open (and keeps blocking) until
    ``approve``. Once approved or bypassed the member can no longer change it; only a missing, expired or still-pending item takes a submission."""
    status = _status(membership, requirement)
    item = item_for(requirement, status)
    if item.display in ("complete", "expiring_soon", "bypassed"):
        raise Invalid("This has been approved and can only be changed through the club.", code="locked")
    today = timezone.localdate()
    if requirement.kind == Kind.QUESTION:
        if raw is _UNSET:
            raise Invalid("Answer the question instead.", {"answer": ["Required."]}, code="use_answer")
        status.answer = validate_answer(requirement, raw)
    else:
        if document is None and not status.document:
            raise Invalid("This requirement needs a document.", {"document": ["Required."]})
        if document is not None:
            check_document(document)
            if status.document:
                status.document.delete(save=False)
            status.document.save(getattr(document, "name", "document"), document, save=False)
    if valid_until is not None and valid_until < today:
        raise Invalid("The expiry date is in the past.", {"valid_until": ["Must be today or later."]})
    status.valid_until = valid_until
    status.is_complete = status.is_bypassed = False
    status.is_pending_review = True
    status.submitted_at = timezone.now()
    status.save()
    audit.record(actor, "onboarding.submitted", member=membership.member, target=status, changes={}, target_label=f"{requirement.name} ({membership.season})")
    return status


@transaction.atomic
def approve(actor, membership: ClubMembership, requirement: OnboardingRequirement) -> MemberRequirementStatus:
    """Staff accept a member's submission: it now counts as complete (with the expiry the member gave, else the requirement's own)."""
    require_admin(actor)
    status = MemberRequirementStatus.objects.filter(membership=membership, requirement=requirement, is_pending_review=True).first()
    if status is None:
        raise Invalid("Nothing is waiting for review.")
    answer_value = status.answer if requirement.kind == Kind.QUESTION else _UNSET
    return _resolve(actor, membership, requirement, "onboarding.approved", complete=True, document=None, note="", valid_until=status.valid_until, answer=answer_value)


@transaction.atomic
def bypass(actor, membership: ClubMembership, requirement: OnboardingRequirement, *, note: str = "") -> MemberRequirementStatus:
    """Mark as not needed for this person. Resolves the requirement just like completing it."""
    require_admin(actor)
    return _resolve(actor, membership, requirement, "onboarding.bypassed", complete=False, document=None, note=note)


@transaction.atomic
def reopen(actor, membership: ClubMembership, requirement: OnboardingRequirement) -> MemberRequirementStatus:
    """Undo a completion/bypass. Deletes the attached document file."""
    require_admin(actor)
    status = MemberRequirementStatus.objects.filter(membership=membership, requirement=requirement).first()
    if status is None or not (status.is_complete or status.is_bypassed or status.is_pending_review):
        raise Invalid("This requirement is not resolved.")
    before = {"is_complete": status.is_complete, "is_bypassed": status.is_bypassed, "document": status.document.name or "", "valid_until": str(status.valid_until or ""), "answer": status.answer}
    if status.document:
        status.document.delete(save=False)
    status.answer = None
    status.is_complete = status.is_bypassed = status.is_pending_review = False
    status.submitted_at = None
    status.completed_at, status.completed_by = None, None
    status.valid_until, status.last_reminder_at, status.last_reminder_kind = None, None, ""
    status.save()
    after = {"is_complete": False, "is_bypassed": False, "document": "", "valid_until": "", "answer": None}
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    audit.record(actor, "onboarding.reopened", member=membership.member, target=status, changes=changes, target_label=f"{requirement.name} ({membership.season})")
    return status


# --- gating ---------------------------------------------------------------------------------------------------------


def blocking_reasons(groups: dict[tuple[str, object], set]) -> dict[tuple[str, object], dict]:
    """Who may not be invited to / selected for an event because of an open requirement, and which ones.

    ``groups`` maps ``(event kind, season id)`` to the member ids to check; the result maps the same keys to ``{member id: [names of the open
    requirements that block]}`` for the blocked ones only. A member is blocked when, in that season, an *active* requirement that lists the kind
    in ``blocked_event_kinds`` is open for them (missing, or completed but expired). A member with no membership that season has no checklist and
    is never blocked. Costs two queries, none when no requirement blocks anything. A kind of ``None`` means any kind: every blocking requirement counts."""
    blocking = list(OnboardingRequirement.objects.filter(is_active=True).exclude(blocked_event_kinds=[]).order_by("name"))
    result = {key: {} for key in groups}
    if not blocking or not groups:
        return result
    today = timezone.localdate()
    seasons = {season for _kind, season in groups if season}
    members = set().union(*groups.values())
    memberships = {(m.member_id, m.season_id): m.pk for m in ClubMembership.objects.filter(season_id__in=seasons, member_id__in=members)}
    statuses: dict[tuple, MemberRequirementStatus] = {(s.membership_id, s.requirement_id): s for s in MemberRequirementStatus.objects.filter(membership_id__in=memberships.values(), requirement__in=blocking)}
    for (kind, season), member_ids in groups.items():
        needed = [r for r in blocking if kind is None or kind in r.blocked_event_kinds]
        if not needed or not season:
            continue
        for member_id in member_ids:
            membership_id = memberships.get((member_id, season))
            if membership_id is None:
                continue
            open_names = [r.name for r in needed if not item_for(r, statuses.get((membership_id, r.pk)), today).resolved]
            if open_names:
                result[(kind, season)][member_id] = open_names
    return result


def blocked_member_ids(groups: dict[tuple[str, object], set]) -> dict[tuple[str, object], set]:
    """The members of ``blocking_reasons`` only: who may not be invited to / selected for an event because of an open requirement."""
    return {key: set(found) for key, found in blocking_reasons(groups).items()}
