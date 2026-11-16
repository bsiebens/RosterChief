"""Families are set up by admins only. A parent/guardian is a guardian only while the family also has a child."""

from collections.abc import Iterable

from django.db import transaction

from members.models import Family, FamilyMembership, Member

from . import audit
from .access import require_admin
from .errors import Invalid

ROLES = {r.value for r in FamilyMembership.FamilyRole}


def _role(role: str) -> str:
    if role not in ROLES:
        raise Invalid(f"Unknown family role {role!r}.", {"role": ["Invalid choice."]})
    return role


@transaction.atomic
def create_family(actor, *, name: str = "", members: Iterable[tuple[Member, str]] = ()) -> Family:
    """Create a family, optionally with ``(member, role)`` pairs."""
    require_admin(actor)
    family = Family.objects.create(name=name.strip())
    audit.record(actor, "family.created", target=family, changes=audit.snapshot(family, ["name"]))
    for member, role in members:
        add_member(actor, family, member, role)
    return family


@transaction.atomic
def rename_family(actor, family: Family, name: str) -> Family:
    require_admin(actor)
    changes = audit.diff(family, {"name": name.strip()})
    if changes:
        family.name = name.strip()
        family.save(update_fields=["name", "modified"])
        audit.record(actor, "family.updated", target=family, changes=changes)
    return family


@transaction.atomic
def delete_family(actor, family: Family) -> None:
    """Delete the family and its links (the people stay). One audit entry per former member."""
    require_admin(actor)
    label = str(family)
    links = list(family.memberships.select_related("member"))
    pk = family.pk
    family.delete()
    for link in links:
        entry = audit.record(actor, "family.deleted", member=link.member, changes={"role": {"from": link.role, "to": None}})
        entry.target_type, entry.target_id, entry.target_label = "family", str(pk), label[:255]
        entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])
    if not links:
        entry = audit.record(actor, "family.deleted")
        entry.target_type, entry.target_id, entry.target_label = "family", str(pk), label[:255]
        entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])


@transaction.atomic
def add_member(actor, family: Family, member: Member, role: str = FamilyMembership.FamilyRole.PARENT) -> FamilyMembership:
    """Put ``member`` in ``family`` with ``role``. A person can be in a family once."""
    require_admin(actor)
    role = _role(role)
    if FamilyMembership.objects.filter(family=family, member=member).exists():
        raise Invalid("This person is already in the family.")
    link = FamilyMembership.objects.create(family=family, member=member, role=role)
    audit.record(actor, "family.member_added", member=member, target=family, changes={"role": {"from": None, "to": role}})
    return link


@transaction.atomic
def remove_member(actor, family: Family, member: Member) -> None:
    require_admin(actor)
    link = FamilyMembership.objects.filter(family=family, member=member).first()
    if link is None:
        raise Invalid("This person is not in the family.")
    role = link.role
    link.delete()
    audit.record(actor, "family.member_removed", member=member, target=family, changes={"role": {"from": role, "to": None}})


@transaction.atomic
def change_role(actor, family: Family, member: Member, role: str) -> FamilyMembership:
    require_admin(actor)
    role = _role(role)
    link = FamilyMembership.objects.filter(family=family, member=member).first()
    if link is None:
        raise Invalid("This person is not in the family.")
    changes = audit.diff(link, {"role": role})
    if changes:
        link.role = role
        link.save(update_fields=["role", "modified"])
        audit.record(actor, "family.role_changed", member=member, target=family, changes=changes)
    return link
