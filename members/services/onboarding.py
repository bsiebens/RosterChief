"""Onboarding requirements and each membership's checklist.

A requirement is *resolved* when completed (and not expired) or bypassed. Resolving never touches ``fee_status`` or
``status``; activation is a separate explicit step (``memberships.activate``).

Kinds: a requirement is a *document* (received, optionally with a file) or a *question* (extra information, completed by
answering it; ``question_type`` text / textarea / number / date / choice / multichoice / yes_no). An answer is validated with the
same cleaning code the form builder uses, stored on the status row (``answer``) and completes the item; expiry, reminders,
bypass, reopen (which clears the answer) and event gating work the same for both kinds.

Expiry: a requirement may be valid for N months (``valid_for_months``); a completion then carries a ``valid_until`` date
(from the requirement, or set by staff). Once that date has passed the item reads as open again -- it blocks exactly like a
missing one. ``ChecklistItem.display`` is the finer label for screens: missing / pending / declined / requested / complete /
expiring_soon / expired / bypassed (``state`` stays open / complete / bypassed).

Review: a hand-in waits for staff (``review_state`` pending). Staff approve it, decline it with a reason the member sees (the member
hands in a new version) or, at any time, send a free request ("please provide / update ..."). Every change is written to the
``RequirementEvent`` history of the status row and the member (and guardians) are notified. A member may also hand in a renewal while the
current completion is still valid (expiring soon, or after a request): the old completion stands until the renewal is approved."""

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
from members.models import ClubMembership, Member, MemberRequirementStatus, OnboardingRequirement, RequirementEvent

from . import audit
from .access import require_admin
from .errors import Invalid

REQUIREMENT_FIELDS = ("name", "description", "kind", "question_type", "options", "min_value", "max_value", "requires_document", "blocked_event_kinds", "is_active", "valid_for_months", "reminder_days_before")
MAX_VALID_MONTHS = 120
MAX_REMINDER_DAYS = 365
Kind = OnboardingRequirement.Kind
Review = MemberRequirementStatus.ReviewState
Ev = RequirementEvent.Kind
QuestionType = OnboardingRequirement.QuestionType
CHOICE_TYPES = (QuestionType.CHOICE, QuestionType.MULTICHOICE)
_TRUE = {"true", "yes", "y", "1", "on", "ja", "oui"}
_FALSE = {"false", "no", "n", "0", "off", "nee", "non"}


@dataclass
class ChecklistItem:
    requirement: OnboardingRequirement
    state: str  # "open" | "complete" | "bypassed"; an expired completion is "open"
    status: MemberRequirementStatus | None = None
    #: missing / pending / declined / requested / complete / expiring_soon / expired / bypassed
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
    """The checklist item for one requirement and its (possibly missing) status row. A completion that is still valid stays ``complete``
    (resolved) while a renewal waits for review or staff asked for an update; the display then says what is going on."""
    today = today or timezone.localdate()
    if status and status.is_bypassed:
        return ChecklistItem(requirement, "bypassed", status, "bypassed")
    if status is None:
        return ChecklistItem(requirement, "open", status, "missing")
    expired = status.is_complete and status.valid_until is not None and status.valid_until < today
    state = "complete" if status.is_complete and not expired else "open"
    if status.is_pending_review:
        return ChecklistItem(requirement, state, status, "pending")  # handed in, not approved yet
    if status.review_state in (Review.DECLINED, Review.REQUESTED):
        return ChecklistItem(requirement, state, status, status.review_state)
    if status.is_complete:
        if expired:
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


def _resolve(actor, membership, requirement, action: str, *, complete: bool, document: File | None, note: str, valid_until: datetime.date | None = None, answer: Any = _UNSET, event: str = Ev.COMPLETED) -> MemberRequirementStatus:
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
    status.review_state, status.review_note, status.submitted_valid_until = (Review.APPROVED if complete else Review.NONE), "", None
    status.completed_at = timezone.now()
    status.completed_by = actor
    status.last_reminder_at, status.last_reminder_kind = None, ""  # a fresh completion starts a fresh reminder cycle
    if note:
        status.note = note
    status.save()
    log_event(status, event, actor, note if event != Ev.APPROVED else "")
    after = {"is_complete": status.is_complete, "is_bypassed": status.is_bypassed, "note": status.note, "document": status.document.name or "", "valid_until": str(status.valid_until or ""), "answer": status.answer}
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    audit.record(actor, action, member=membership.member, target=status, changes=changes, target_label=f"{requirement.name} ({membership.season})")
    return status


@transaction.atomic
def mark_complete(actor, membership: ClubMembership, requirement: OnboardingRequirement, *, document: File | None = None, note: str = "", valid_until: datetime.date | None = None) -> MemberRequirementStatus:
    """Mark received. A requirement with ``requires_document`` needs ``document`` (or one already attached). ``valid_until``
    overrides the requirement's own validity; completing an item again (new file, new date) simply updates it."""
    require_admin(actor)
    return _resolve(actor, membership, requirement, "onboarding.completed", event=Ev.COMPLETED, complete=True, document=document, note=note, valid_until=valid_until)


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
        return _resolve(actor, membership, requirement, "onboarding.answered", event=Ev.COMPLETED, complete=True, document=None, note=note, valid_until=valid_until, answer=cleaned)


@transaction.atomic
def submit_as(actor, membership: ClubMembership, requirement: OnboardingRequirement, *, document: File | None = None, raw: Any = _UNSET, valid_until: datetime.date | None = None) -> MemberRequirementStatus:
    """A member (or their guardian) hands in a document or an answer. It waits for staff: a missing, expired or declined item stays open (and keeps
    blocking) until ``approve``. Handing in while the current completion is still valid (expiring soon, or staff asked for an update) is an early
    renewal: the old completion stands until the renewal is approved. Approved items that are not due and bypassed ones are locked."""
    status = _status(membership, requirement)
    item = item_for(requirement, status)
    if item.display in ("complete", "bypassed"):
        raise Invalid("This has been approved and can only be changed through the club.", code="locked")
    today = timezone.localdate()
    renewal = item.state == "complete"  # a valid completion stays in force
    if requirement.kind == Kind.QUESTION:
        if raw is _UNSET:
            raise Invalid("Answer the question instead.", {"answer": ["Required."]}, code="use_answer")
        cleaned = validate_answer(requirement, raw)
    else:
        if document is None and not status.document:
            raise Invalid("This requirement needs a document.", {"document": ["Required."]})
        if document is not None:
            check_document(document)
    if valid_until is not None and valid_until < today:
        raise Invalid("The expiry date is in the past.", {"valid_until": ["Must be today or later."]})
    if requirement.kind == Kind.QUESTION:
        status.answer = cleaned
    elif document is not None:
        if status.document:
            status.document.delete(save=False)
        status.document.save(getattr(document, "name", "document"), document, save=False)
    status.submitted_valid_until = valid_until
    if not renewal:
        status.valid_until = valid_until
        status.is_complete = status.is_bypassed = False
    status.is_pending_review = True
    status.review_state, status.review_note = Review.PENDING, ""
    status.submitted_at = timezone.now()
    status.save()
    log_event(status, Ev.SUBMITTED, actor)
    audit.record(actor, "onboarding.submitted", member=membership.member, target=status, changes={}, target_label=f"{requirement.name} ({membership.season})")
    _tell_staff(actor, membership, requirement)
    return status


# --- review history and messages to the member -----------------------------------------------------------------------


def log_event(status: MemberRequirementStatus, kind: str, actor=None, note: str = "") -> RequirementEvent:
    """Append to the status row's history. ``actor`` is a user (or None for the system / a registrant without a login)."""
    member = Member.objects.filter(user=actor).first() if actor is not None and getattr(actor, "pk", None) else None
    name = ""
    if actor is not None and getattr(actor, "pk", None):
        name = (member and str(member)) or getattr(actor, "get_full_name", lambda: "")() or getattr(actor, "email", "") or ""
    return RequirementEvent.objects.create(status=status, kind=kind, actor=member, actor_name=name[:200], note=note)


#: What the member reads, per language: title and body (``{name}`` the requirement, ``{who}`` whose, ``{note}`` the reason / message).
_MESSAGES = {
    "nl": {
        "approved": ("Goedgekeurd: {name}", "{who}: de club heeft “{name}” goedgekeurd."),
        "declined": ("Geweigerd: {name}", "{who}: de club heeft “{name}” niet aanvaard. Reden: {note}"),
        "requested": ("De club vraagt iets: {name}", "{who}: {note}"),
        "sent_file": ("De club stuurde je een document: {name}", "{who}: download het document, vul het in en bezorg het terug. {note}"),
        "who_self": "Jij",
    },
    "en": {
        "approved": ("Approved: {name}", "{who}: the club approved “{name}”."),
        "declined": ("Declined: {name}", "{who}: the club did not accept “{name}”. Reason: {note}"),
        "requested": ("The club asks for something: {name}", "{who}: {note}"),
        "sent_file": ("The club sent you a document: {name}", "{who}: download the document, fill it in and hand it back. {note}"),
        "who_self": "You",
    },
    "fr": {
        "approved": ("Approuvé : {name}", "{who} : le club a approuvé « {name} »."),
        "declined": ("Refusé : {name}", "{who} : le club n’a pas accepté « {name} ». Motif : {note}"),
        "requested": ("Le club demande quelque chose : {name}", "{who} : {note}"),
        "sent_file": ("Le club vous a envoyé un document : {name}", "{who} : téléchargez le document, remplissez-le et renvoyez-le. {note}"),
        "who_self": "Vous",
    },
}


def _tell_member(actor, membership: ClubMembership, requirement: OnboardingRequirement, kind: str, note: str = "") -> None:
    """Inbox plus push (category "form") for the member and, for a child, their guardians, with a link to the requirements page. Never blocks the action."""
    import logging

    from events.services.attendance import current_club
    from notifications import inbox

    from . import recipients

    club = current_club()
    if club is None:
        return
    try:
        rows = recipients.recipients(membership.member)
        languages = dict(Member.objects.filter(user__in=[u for u, _child in rows]).values_list("user_id", "language"))
        for user, child in rows:
            if actor is not None and user.pk == getattr(actor, "pk", None):
                continue
            code = (languages.get(user.pk) or "").split("-")[0].lower()
            texts = _MESSAGES.get(code, _MESSAGES["nl"])
            title, body = texts[kind]
            fmt = {"name": requirement.name, "who": texts["who_self"] if child is None else child.first_name, "note": note}
            inbox.notify(user, club, title=title.format(**fmt), body=body.format(**fmt).strip(), kind=inbox.Kind.INFO if kind == "approved" else inbox.Kind.WARNING, href="/parent?tab=payments", push=True, category="form")
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("onboarding.notify_member_failed")


def reviewers():
    """The users of this club who may review hand-ins: everyone holding ``members.manage_members`` (the Owner role included)."""
    from tenant_users.permissions.models import UserTenantPermissions

    from .access import can_manage_members

    return [perms.profile for perms in UserTenantPermissions.objects.select_related("profile").filter(profile__is_active=True) if can_manage_members(perms.profile)]


def _tell_staff(actor, membership: ClubMembership, requirement: OnboardingRequirement) -> None:
    """Inbox entry plus push for each reviewer (the submitter themself, if they are one, is left out). Never blocks the hand-in."""
    from events.services.attendance import current_club
    from notifications import inbox

    club = current_club()
    if club is None:
        return
    title = f"{membership.member}: {requirement.name}"
    for user in reviewers():
        if actor is not None and user.pk == getattr(actor, "pk", None):
            continue
        try:
            inbox.notify(user, club, title=title, body="Handed in and waiting for your review.", kind=inbox.Kind.INFO, href=f"/members/{membership.member_id}", push=True, category="admin")
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception("onboarding.notify_failed")


@transaction.atomic
def approve(actor, membership: ClubMembership, requirement: OnboardingRequirement) -> MemberRequirementStatus:
    """Staff accept a member's submission: it now counts as complete (with the expiry the member gave, else the requirement's own)."""
    require_admin(actor)
    status = MemberRequirementStatus.objects.filter(membership=membership, requirement=requirement, is_pending_review=True).first()
    if status is None:
        raise Invalid("Nothing is waiting for review.")
    answer_value = status.answer if requirement.kind == Kind.QUESTION else _UNSET
    until = status.submitted_valid_until if status.is_complete else status.valid_until  # a renewal carries its own expiry
    status = _resolve(actor, membership, requirement, "onboarding.approved", event=Ev.APPROVED, complete=True, document=None, note="", valid_until=until, answer=answer_value)
    _tell_member(actor, membership, requirement, "approved")
    return status


def _clean_text(value: str, field: str, message: str) -> str:
    value = (value or "").strip()
    if not value:
        raise Invalid(message, {field: ["This field is required."]})
    return value[:2000]


@transaction.atomic
def decline(actor, membership: ClubMembership, requirement: OnboardingRequirement, *, reason: str) -> MemberRequirementStatus:
    """Staff do not accept what was handed in. The reason is shown to the member, who can hand in a new version (which waits for review again).
    A completion that was still valid (an early renewal being declined) stays in force."""
    require_admin(actor)
    reason = _clean_text(reason, "reason", "Say why this is declined.")
    status = MemberRequirementStatus.objects.filter(membership=membership, requirement=requirement, is_pending_review=True).first()
    if status is None:
        raise Invalid("Nothing is waiting for review.")
    status.is_pending_review = False
    status.submitted_valid_until = None
    status.review_state, status.review_note = Review.DECLINED, reason
    status.save()
    log_event(status, Ev.DECLINED, actor, reason)
    audit.record(actor, "onboarding.declined", member=membership.member, target=status, changes={"review_note": {"from": "", "to": reason}}, target_label=f"{requirement.name} ({membership.season})")
    _tell_member(actor, membership, requirement, "declined", reason)
    return status


@transaction.atomic
def request_update(actor, membership: ClubMembership, requirement: OnboardingRequirement, *, message: str) -> MemberRequirementStatus:
    """Staff ask the member for something (or to update something), at any time. The message is shown on the requirement; what was handed in
    (if anything) no longer waits for review. Not for an item marked not needed: reopen it first."""
    require_admin(actor)
    message = _clean_text(message, "message", "Say what you need.")
    status = _status(membership, requirement)
    if status.is_bypassed:
        raise Invalid("This is marked as not needed for this person. Reopen it first.")
    status.is_pending_review = False
    status.submitted_valid_until = None
    status.review_state, status.review_note = Review.REQUESTED, message
    status.save()
    log_event(status, Ev.REQUESTED, actor, message)
    audit.record(actor, "onboarding.requested", member=membership.member, target=status, changes={"review_note": {"from": "", "to": message}}, target_label=f"{requirement.name} ({membership.season})")
    _tell_member(actor, membership, requirement, "requested", message)
    return status


def _check_sendable(status: MemberRequirementStatus, requirement: OnboardingRequirement) -> None:
    if requirement.kind != Kind.DOCUMENT:
        raise Invalid("Only a document can come with a file from the club.", code="not_a_document")
    if item_for(requirement, status).display in ("complete", "bypassed"):
        raise Invalid("This has been approved already.", code="locked")


@transaction.atomic
def send_file(actor, membership: ClubMembership, requirement: OnboardingRequirement, document: File, *, note: str = "") -> MemberRequirementStatus:
    """Staff hand this one member a personalised document for the requirement (e.g. a pre-filled form to fill in and hand back). It replaces an
    earlier one; the member (and their guardians) are told and can download it. Not possible once the item is approved."""
    require_admin(actor)
    check_document(document)
    status = _status(membership, requirement)
    _check_sendable(status, requirement)
    if status.sent_file:
        status.sent_file.delete(save=False)
    status.sent_file.save(getattr(document, "name", "document"), document, save=False)
    status.sent_at, status.sent_note = timezone.now(), (note or "").strip()[:2000]
    status.save()
    log_event(status, Ev.SENT_FILE, actor, status.sent_note)
    audit.record(actor, "onboarding.file_sent", member=membership.member, target=status, changes={"sent_file": {"from": "", "to": status.sent_file.name}}, target_label=f"{requirement.name} ({membership.season})")
    _tell_member(actor, membership, requirement, "sent_file", status.sent_note)
    return status


@transaction.atomic
def remove_sent_file(actor, membership: ClubMembership, requirement: OnboardingRequirement) -> MemberRequirementStatus:
    """Take back the file sent to the member (before the item is approved)."""
    require_admin(actor)
    status = MemberRequirementStatus.objects.filter(membership=membership, requirement=requirement).first()
    if status is None or not status.sent_file:
        raise Invalid("No document was sent.")
    _check_sendable(status, requirement)
    name = status.sent_file.name
    status.sent_file.delete(save=False)
    status.sent_file, status.sent_at, status.sent_note = "", None, ""
    status.save()
    audit.record(actor, "onboarding.file_removed", member=membership.member, target=status, changes={"sent_file": {"from": name, "to": ""}}, target_label=f"{requirement.name} ({membership.season})")
    return status


@transaction.atomic
def bypass(actor, membership: ClubMembership, requirement: OnboardingRequirement, *, note: str = "") -> MemberRequirementStatus:
    """Mark as not needed for this person. Resolves the requirement just like completing it."""
    require_admin(actor)
    return _resolve(actor, membership, requirement, "onboarding.bypassed", event=Ev.BYPASSED, complete=False, document=None, note=note)


@transaction.atomic
def reopen(actor, membership: ClubMembership, requirement: OnboardingRequirement) -> MemberRequirementStatus:
    """Undo a completion/bypass. Deletes the attached document file."""
    require_admin(actor)
    status = MemberRequirementStatus.objects.filter(membership=membership, requirement=requirement).first()
    if status is None or not (status.is_complete or status.is_bypassed or status.is_pending_review or status.review_state in (Review.DECLINED, Review.REQUESTED)):
        raise Invalid("This requirement is not resolved.")
    before = {"is_complete": status.is_complete, "is_bypassed": status.is_bypassed, "document": status.document.name or "", "valid_until": str(status.valid_until or ""), "answer": status.answer}
    if status.document:
        status.document.delete(save=False)
    status.answer = None
    status.is_complete = status.is_bypassed = status.is_pending_review = False
    status.submitted_at = None
    status.review_state, status.review_note, status.submitted_valid_until = Review.NONE, "", None
    status.completed_at, status.completed_by = None, None
    status.valid_until, status.last_reminder_at, status.last_reminder_kind = None, None, ""
    status.save()
    log_event(status, Ev.REOPENED, actor)
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
