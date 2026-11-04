"""Onboarding requirements and each membership's checklist.

A requirement is *resolved* when completed or bypassed. Resolving never touches ``fee_status`` or ``status``;
activation is a separate explicit step (``memberships.activate``)."""

from dataclasses import dataclass
from typing import Any

from django.core.files import File
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from events.models import Event
from members.models import ClubMembership, MemberRequirementStatus, OnboardingRequirement

from . import audit
from .access import require_admin
from .errors import Invalid

REQUIREMENT_FIELDS = ("name", "description", "requires_document", "blocked_event_kinds", "is_active")


@dataclass
class ChecklistItem:
    requirement: OnboardingRequirement
    state: str  # "open" | "complete" | "bypassed"
    status: MemberRequirementStatus | None = None

    @property
    def resolved(self) -> bool:
        return self.state != "open"


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
def create_requirement(actor, *, name: str, description: str = "", requires_document: bool = False, blocked_event_kinds=(), is_active: bool = True) -> OnboardingRequirement:
    """Define a requirement. ``blocked_event_kinds`` must be ``Event.EventKind`` values."""
    require_admin(actor)
    values = _validated({"name": name, "description": description, "requires_document": requires_document, "blocked_event_kinds": blocked_event_kinds, "is_active": is_active})
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


def checklist_for(membership: ClubMembership) -> list[ChecklistItem]:
    """One item per active requirement, with its state for this membership."""
    statuses = {s.requirement_id: s for s in membership.requirement_statuses.all()}
    items = []
    for requirement in open_requirements():
        status = statuses.get(requirement.pk)
        state = "complete" if status and status.is_complete else "bypassed" if status and status.is_bypassed else "open"
        items.append(ChecklistItem(requirement, state, status))
    return items


def unresolved(membership: ClubMembership) -> list[OnboardingRequirement]:
    """Active requirements this membership has neither completed nor bypassed."""
    return [i.requirement for i in checklist_for(membership) if not i.resolved]


def _status(membership: ClubMembership, requirement: OnboardingRequirement) -> MemberRequirementStatus:
    if not requirement.is_active:
        raise Invalid("This requirement is no longer active.")
    status, _ = MemberRequirementStatus.objects.get_or_create(membership=membership, requirement=requirement)
    return status


def _resolve(actor, membership, requirement, action: str, *, complete: bool, document: File | None, note: str) -> MemberRequirementStatus:
    status = _status(membership, requirement)
    before = {"is_complete": status.is_complete, "is_bypassed": status.is_bypassed, "note": status.note, "document": status.document.name or ""}
    if complete:
        if requirement.requires_document and document is None and not status.document:
            raise Invalid("This requirement needs a document.", {"document": ["Required."]})
        if document is not None:
            status.document.save(getattr(document, "name", "document"), document, save=False)
    status.is_complete, status.is_bypassed = complete, not complete
    status.completed_at = timezone.now()
    status.completed_by = actor
    if note:
        status.note = note
    status.save()
    after = {"is_complete": status.is_complete, "is_bypassed": status.is_bypassed, "note": status.note, "document": status.document.name or ""}
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    audit.record(actor, action, member=membership.member, target=status, changes=changes, target_label=f"{requirement.name} ({membership.season})")
    return status


@transaction.atomic
def mark_complete(actor, membership: ClubMembership, requirement: OnboardingRequirement, *, document: File | None = None, note: str = "") -> MemberRequirementStatus:
    """Mark received. A requirement with ``requires_document`` needs ``document`` (or one already attached)."""
    require_admin(actor)
    return _resolve(actor, membership, requirement, "onboarding.completed", complete=True, document=document, note=note)


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
    before = {"is_complete": status.is_complete, "is_bypassed": status.is_bypassed, "document": status.document.name or ""}
    if status.document:
        status.document.delete(save=False)
    status.is_complete = status.is_bypassed = False
    status.completed_at, status.completed_by = None, None
    status.save()
    after = {"is_complete": False, "is_bypassed": False, "document": ""}
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    audit.record(actor, "onboarding.reopened", member=membership.member, target=status, changes=changes, target_label=f"{requirement.name} ({membership.season})")
    return status
