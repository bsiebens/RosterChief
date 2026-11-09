"""Onboarding requirements and each membership's checklist.

A requirement is *resolved* when completed (and not expired) or bypassed. Resolving never touches ``fee_status`` or
``status``; activation is a separate explicit step (``memberships.activate``).

Expiry: a requirement may be valid for N months (``valid_for_months``); a completion then carries a ``valid_until`` date
(from the requirement, or set by staff). Once that date has passed the item reads as open again -- it blocks exactly like a
missing one. ``ChecklistItem.display`` is the finer label for screens: missing / complete / expiring_soon / expired /
bypassed (``state`` stays open / complete / bypassed)."""

import datetime
import os
from dataclasses import dataclass
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

REQUIREMENT_FIELDS = ("name", "description", "requires_document", "blocked_event_kinds", "is_active", "valid_for_months", "reminder_days_before")
MAX_VALID_MONTHS = 120
MAX_REMINDER_DAYS = 365


@dataclass
class ChecklistItem:
    requirement: OnboardingRequirement
    state: str  # "open" | "complete" | "bypassed"; an expired completion is "open"
    status: MemberRequirementStatus | None = None
    #: missing / complete / expiring_soon / expired / bypassed
    display: str = "missing"

    @property
    def resolved(self) -> bool:
        return self.state != "open"

    @property
    def valid_until(self) -> datetime.date | None:
        return self.status.valid_until if self.status else None

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
    if "blocked_event_kinds" in values:
        kinds = list(values["blocked_event_kinds"])
        bad = [k for k in kinds if k not in Event.EventKind.values]
        if bad:
            raise Invalid(f"Unknown event kinds: {bad}", {"blocked_event_kinds": ["Invalid choice."]})
        values["blocked_event_kinds"] = kinds
    return values


def _name_free(name: str, exclude=None) -> None:
    qs = OnboardingRequirement.objects.filter(name__iexact=name)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    if qs.exists():
        raise Invalid("A requirement with this name already exists.", {"name": ["Already in use."]})


@transaction.atomic
def create_requirement(actor, *, name: str, description: str = "", requires_document: bool = False, blocked_event_kinds=(), is_active: bool = True, valid_for_months: int | None = None, reminder_days_before: int = 30) -> OnboardingRequirement:
    """Define a requirement. ``blocked_event_kinds`` must be ``Event.EventKind`` values."""
    require_admin(actor)
    values = _validated(
        {
            "name": name, "description": description, "requires_document": requires_document, "blocked_event_kinds": blocked_event_kinds, "is_active": is_active,
            "valid_for_months": valid_for_months, "reminder_days_before": reminder_days_before,
        }
    )  # fmt: skip
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


def _expiry(requirement: OnboardingRequirement, on: datetime.date, valid_until: datetime.date | None) -> datetime.date | None:
    """The expiry to store: what staff entered, else ``valid_for_months`` after ``on``, else none."""
    if valid_until is not None:
        return valid_until
    if requirement.valid_for_months:
        return on + relativedelta(months=requirement.valid_for_months)
    return None


def _resolve(actor, membership, requirement, action: str, *, complete: bool, document: File | None, note: str, valid_until: datetime.date | None = None) -> MemberRequirementStatus:
    status = _status(membership, requirement)
    before = {"is_complete": status.is_complete, "is_bypassed": status.is_bypassed, "note": status.note, "document": status.document.name or "", "valid_until": str(status.valid_until or "")}
    today = timezone.localdate()
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
    status.is_complete, status.is_bypassed = complete, not complete
    status.completed_at = timezone.now()
    status.completed_by = actor
    status.last_reminder_at, status.last_reminder_kind = None, ""  # a fresh completion starts a fresh reminder cycle
    if note:
        status.note = note
    status.save()
    after = {"is_complete": status.is_complete, "is_bypassed": status.is_bypassed, "note": status.note, "document": status.document.name or "", "valid_until": str(status.valid_until or "")}
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
def bypass(actor, membership: ClubMembership, requirement: OnboardingRequirement, *, note: str = "") -> MemberRequirementStatus:
    """Mark as not needed for this person. Resolves the requirement just like completing it."""
    require_admin(actor)
    return _resolve(actor, membership, requirement, "onboarding.bypassed", complete=False, document=None, note=note)


@transaction.atomic
def reopen(actor, membership: ClubMembership, requirement: OnboardingRequirement) -> MemberRequirementStatus:
    """Undo a completion/bypass. Deletes the attached document file."""
    require_admin(actor)
    status = MemberRequirementStatus.objects.filter(membership=membership, requirement=requirement).first()
    if status is None or not (status.is_complete or status.is_bypassed):
        raise Invalid("This requirement is not resolved.")
    before = {"is_complete": status.is_complete, "is_bypassed": status.is_bypassed, "document": status.document.name or "", "valid_until": str(status.valid_until or "")}
    if status.document:
        status.document.delete(save=False)
    status.is_complete = status.is_bypassed = False
    status.completed_at, status.completed_by = None, None
    status.valid_until, status.last_reminder_at, status.last_reminder_kind = None, None, ""
    status.save()
    after = {"is_complete": False, "is_bypassed": False, "document": "", "valid_until": ""}
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    audit.record(actor, "onboarding.reopened", member=membership.member, target=status, changes=changes, target_label=f"{requirement.name} ({membership.season})")
    return status


# --- gating ---------------------------------------------------------------------------------------------------------


def blocked_member_ids(groups: dict[tuple[str, object], set]) -> dict[tuple[str, object], set]:
    """Who may not be invited to / selected for an event because of an open requirement.

    ``groups`` maps ``(event kind, season id)`` to the member ids to check; the result maps the same keys to the blocked
    subset. A member is blocked when, in that season, an *active* requirement that lists the kind in ``blocked_event_kinds``
    is open for them (missing, or completed but expired). A member with no membership that season has no checklist and is
    never blocked. Costs two queries, none when no requirement blocks anything."""
    blocking = list(OnboardingRequirement.objects.filter(is_active=True).exclude(blocked_event_kinds=[]))
    result = {key: set() for key in groups}
    if not blocking or not groups:
        return result
    today = timezone.localdate()
    seasons = {season for _kind, season in groups if season}
    members = set().union(*groups.values())
    memberships = {(m.member_id, m.season_id): m.pk for m in ClubMembership.objects.filter(season_id__in=seasons, member_id__in=members)}
    statuses: dict[tuple, MemberRequirementStatus] = {(s.membership_id, s.requirement_id): s for s in MemberRequirementStatus.objects.filter(membership_id__in=memberships.values(), requirement__in=blocking)}
    for (kind, season), member_ids in groups.items():
        needed = [r for r in blocking if kind in r.blocked_event_kinds]
        if not needed or not season:
            continue
        for member_id in member_ids:
            membership_id = memberships.get((member_id, season))
            if membership_id is None:
                continue
            if any(not item_for(r, statuses.get((membership_id, r.pk)), today).resolved for r in needed):
                result[(kind, season)].add(member_id)
    return result
