"""Generic named groups of members (not team rosters)."""

from django.db import transaction

from members.models import Group, GroupMembership, Member

from . import audit
from .access import require_admin, require_group_manager
from .errors import Invalid


def _unique_name(name: str, exclude=None) -> str:
    name = name.strip()
    if not name:
        raise Invalid("A group needs a name.", {"name": ["Required."]})
    qs = Group.objects.filter(name__iexact=name)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    if qs.exists():
        raise Invalid("A group with this name already exists.", {"name": ["Already in use."]})
    return name


@transaction.atomic
def create_group(actor, name: str) -> Group:
    require_admin(actor)
    group = Group.objects.create(name=_unique_name(name))
    audit.record(actor, "group.created", target=group, changes=audit.snapshot(group, ["name"]))
    return group


@transaction.atomic
def rename_group(actor, group: Group, name: str) -> Group:
    require_admin(actor)
    name = _unique_name(name, exclude=group)
    changes = audit.diff(group, {"name": name})
    if changes:
        group.name = name
        group.save(update_fields=["name", "modified"])
        audit.record(actor, "group.updated", target=group, changes=changes)
    return group


@transaction.atomic
def delete_group(actor, group: Group) -> None:
    """Delete the group and its links (the people stay)."""
    require_admin(actor)
    pk, label = group.pk, group.name
    group.delete()
    entry = audit.record(actor, "group.deleted", changes={"name": {"from": label, "to": None}})
    entry.target_type, entry.target_id, entry.target_label = "group", str(pk), label[:255]
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])


@transaction.atomic
def add_member(actor, group: Group, member: Member, *, is_admin: bool = False, must_respond: bool = True) -> GroupMembership:
    """Members managers and the group's own admins may add people; only members managers may add an admin."""
    require_group_manager(actor, group)
    if is_admin:
        require_admin(actor)
    if GroupMembership.objects.filter(group=group, member=member).exists():
        raise Invalid("This person is already in the group.")
    link = GroupMembership.objects.create(group=group, member=member, is_admin=is_admin, must_respond=must_respond)
    audit.record(actor, "group.member_added", member=member, target=group, changes={"group": {"from": None, "to": group.name}})
    return link


@transaction.atomic
def update_member(actor, group: Group, member: Member, *, is_admin: bool | None = None, must_respond: bool | None = None) -> GroupMembership:
    """Appoint / dismiss a group admin (members managers only) and say whether the person must answer the group's invites
    (members managers and the group's admins). The next upcoming events' attendance rows follow (events.signals)."""
    require_group_manager(actor, group)
    link = GroupMembership.objects.filter(group=group, member=member).first()
    if link is None:
        raise Invalid("This person is not in the group.")
    changes = {}
    if is_admin is not None and is_admin != link.is_admin:
        require_admin(actor)
        changes["is_admin"] = {"from": link.is_admin, "to": is_admin}
        link.is_admin = is_admin
    if must_respond is not None and must_respond != link.must_respond:
        changes["must_respond"] = {"from": link.must_respond, "to": must_respond}
        link.must_respond = must_respond
    if changes:
        link.save()
        audit.record(actor, "group.member_updated", member=member, target=group, changes=changes)
    return link


@transaction.atomic
def remove_member(actor, group: Group, member: Member) -> None:
    require_group_manager(actor, group)
    deleted, _ = GroupMembership.objects.filter(group=group, member=member).delete()
    if not deleted:
        raise Invalid("This person is not in the group.")
    audit.record(actor, "group.member_removed", member=member, target=group, changes={"group": {"from": group.name, "to": None}})
