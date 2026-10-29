"""Groups: generic named collections of members (management /groups screens and the management API)."""

from django.db import transaction
from django.db.models import Count

from members.models import Group, GroupMembership


def groups_with_counts(club):
    """The club's groups by name, each with ``member_count``.

    order_by is explicit: the Count() annotation forces a GROUP BY, which Django doesn't apply the
    model's default ordering to, and pagination needs a real order to split on."""
    return Group.objects.filter(club=club).annotate(member_count=Count("memberships", distinct=True)).order_by("name")


def group_member_ids(group) -> set:
    return set(GroupMembership.objects.filter(group=group).values_list("member_id", flat=True))


def add_members_to_group(group, members) -> list[GroupMembership]:
    """Put ``members`` in ``group`` (the caller has checked they are visible and not in it already)."""
    with transaction.atomic():
        return GroupMembership.objects.bulk_create([GroupMembership(group=group, member=member) for member in members])


def remove_group_membership(membership) -> None:
    membership.delete()
