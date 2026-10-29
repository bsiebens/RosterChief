"""Management API: groups, generic named collections of members (API.md §17).

The JSON counterpart of the ``/manage/groups`` screens (management.views.Group*View). Every route
is for ``can_manage_members`` (club admin or member admin), as ``MemberAdminRequiredMixin``; the
bulk-add candidates are ``members_visible_to`` like the web formset. The logic is
members.services.groups.
"""

import uuid
from typing import Annotated

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from api.core.errors import ApiError
from api.core.forms import form_error
from api.core.idempotency import idempotent
from api.core.pagination import paginate_keyset
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas import groups as s
from club.services.access import can_manage_members, members_visible_to
from management.forms import GroupForm
from members.models import Group, GroupMembership
from members.services.groups import add_members_to_group, group_member_ids, groups_with_counts, remove_group_membership

router = Router(tags=["panel: groups"])

PREFIX = "/manage/groups"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}


def _club(request):
    club = panel_club(request)
    require(request, club, can_manage_members)
    return club


def _group(club, group_id) -> Group:
    return get_object_or_404(Group.objects.filter(club=club), pk=group_id)


def _out(group) -> dict:
    return {"id": group.pk, "name": group.name, "member_count": group.member_count}


def _detail(club, group_id) -> dict:
    group = get_object_or_404(groups_with_counts(club), pk=group_id)
    rows = GroupMembership.objects.filter(group=group).select_related("member").order_by("member__last_name", "member__first_name", "pk")
    members = [{"membership_id": row.pk, "member_id": row.member_id, "first_name": row.member.first_name, "last_name": row.member.last_name} for row in rows]
    return {**_out(group), "members": members}


def _save(club, name, instance) -> Group:
    form = GroupForm({"name": name}, club=club, instance=instance)
    if not form.is_valid():
        raise form_error(form)
    try:
        with transaction.atomic():
            return form.save()
    except IntegrityError:  # lost a race with a same-named group
        raise ApiError(422, "validation_error", fields={"name": [str(_("A group with this name already exists."))]}) from None


@router.get(PREFIX, response={200: s.GroupListOut, **ERRORS}, summary="Groups")
def list_groups(request, q: Annotated[str | None, Query(description="Name contains.")] = None, cursor: str | None = None, limit: int | None = None):
    """By name, with member counts. Club admins and member admins."""
    club = _club(request)
    groups = groups_with_counts(club)
    if q and q.strip():
        groups = groups.filter(name__icontains=q.strip())
    page = paginate_keyset(groups, fields=["name", "pk"], cursor=cursor, limit=limit)
    return {"items": [_out(group) for group in page.items], "next_cursor": page.next_cursor}


@router.post(PREFIX, response={201: s.GroupDetailOut, **ERRORS, **INVALID}, summary="Create a group")
def create_group(request, payload: s.GroupIn):
    club = _club(request)

    def work():
        group = _save(club, payload.name, Group(club=club))
        return 201, _detail(club, group.pk)

    return idempotent(request, work)


@router.get(PREFIX + "/{uuid:group_id}", response={200: s.GroupDetailOut, **ERRORS}, summary="One group with its members")
def get_group(request, group_id: uuid.UUID):
    club = _club(request)
    return _detail(club, group_id)


@router.patch(PREFIX + "/{uuid:group_id}", response={200: s.GroupDetailOut, **ERRORS, **INVALID}, summary="Rename a group")
def update_group(request, group_id: uuid.UUID, payload: s.GroupPatch):
    club = _club(request)
    group = _group(club, group_id)
    if payload.name is not None:
        _save(club, payload.name, group)
    return _detail(club, group_id)


@router.delete(PREFIX + "/{uuid:group_id}", response={204: None, **ERRORS}, summary="Delete a group")
def delete_group(request, group_id: uuid.UUID):
    """Its memberships go with it; the members themselves are untouched."""
    club = _club(request)
    _group(club, group_id).delete()
    return 204, None


@router.post(PREFIX + "/{uuid:group_id}/members", response={201: s.GroupDetailOut, **ERRORS, **INVALID}, summary="Add members to a group")
def add_members(request, group_id: uuid.UUID, payload: s.GroupMembersIn):
    """All or nothing: an unknown or invisible member, one already in the group, or a repeat is a 422 on `member_ids`."""
    club = _club(request)
    group = _group(club, group_id)

    def work():
        ids = payload.member_ids
        visible = {member.pk: member for member in members_visible_to(request.user, club, include_guardians=True).filter(pk__in=ids)}
        existing = group_member_ids(group)
        problems = []
        seen = set()
        for pk in ids:
            if pk not in visible:
                problems.append(str(_("%(id)s is not a member you can add.") % {"id": pk}))
            elif pk in existing:
                problems.append(str(_("%(member)s is already in this group.") % {"member": visible[pk]}))
            elif pk in seen:
                problems.append(str(_("%(member)s is listed twice.") % {"member": visible[pk]}))
            seen.add(pk)
        if problems:
            raise ApiError(422, "validation_error", fields={"member_ids": problems})
        add_members_to_group(group, [visible[pk] for pk in ids])
        return 201, _detail(club, group.pk)

    return idempotent(request, work)


@router.delete(PREFIX + "/{uuid:group_id}/members/{uuid:membership_id}", response={204: None, **ERRORS}, summary="Remove a member from a group")
def remove_member(request, group_id: uuid.UUID, membership_id: uuid.UUID):
    """`membership_id` is the `membership_id` of the group's member list."""
    club = _club(request)
    group = _group(club, group_id)
    remove_group_membership(get_object_or_404(GroupMembership.objects.filter(group=group), pk=membership_id))
    return 204, None
