"""When a Member row may be hard-deleted.

Member is the one person record shared by every club (it carries no club FK
of its own), so deleting it cascades through everything hanging off it
anywhere on the platform -- other clubs' memberships, roles, families,
rosters, attendance, evaluations. Every code path that wants to remove a
Member outright (cancelling a one-off sign-up, merging away a duplicate)
asks ``member_is_deletable`` first and falls back to removing only its own
club's rows when the answer is no.
"""

from django.db.models import Q

from club.models import ClubMembership, ClubRole
from members.models import FamilyMembership

#: Reverse relations that carry real history or grants. Each is paired with
#: the lookup from that row to its club, so rows belonging to the club whose
#: memberships are being removed can be set aside (they cascade with the
#: member, same as the membership itself); ``None`` means "any row at all
#: keeps the member" -- shop records are PROTECT-ed and money-related.
_HISTORY_RELATIONS = (
    ("shop_manager_grants", "club"),
    ("evaluation_manager_grants", "club"),
    ("group_memberships", "group__club"),
    ("referee_profiles", "club"),
    ("official_profiles", "club"),
    ("attendances", "event__club"),
    ("lineup_selections", "lineup__event__club"),
    ("referee_assignments", "event__club"),
    ("referee_signups", "event__club"),
    ("official_assignments", "event__club"),
    ("official_signups", "event__club"),
    ("evaluations_received", "club"),
    ("evaluation_notes", "club"),
    ("evaluation_outcomes", "club"),
    ("bug_reports", "club"),
    ("orders", None),
    ("order_items", None),
    ("cart_items", None),
    ("vouchers", None),
    ("applied_discounts", None),
)


def member_is_deletable(member, *, ignoring=()) -> bool:
    """Whether ``member`` can be hard-deleted without losing anything beyond
    the ClubMembership rows in ``ignoring`` (the rows the caller is removing
    anyway -- e.g. the one sign-up being cancelled).

    The clubs/seasons of ``ignoring`` define what counts as "this club's own
    rows": the implicit MEMBER role there, a CHILD row in one of its
    families, and a roster/staff spot for one of those seasons all go with
    the member. Anything else keeps the member:

    - a login (``member.user``) -- a person's account is never deleted as a
      side effect of a club-level action;
    - any other ClubMembership, in this club or any other;
    - any ClubRole other than the implicit MEMBER role of an ignored club;
    - a FamilyMembership in another club's family, or a PARENT/GUARDIAN row
      in a family that still has other members relying on them;
    - a TeamMembership/StaffAssignment outside the ignored clubs/seasons;
    - grants or history in another club, and any shop record at all.

    Pure check -- deletes nothing itself. Callers: club.services.
    cancellation.cancel_membership, club.services.signup_linking.
    link_to_existing_member."""
    if member.pk is None:
        return False
    if member.user_id is not None:
        return False

    ignoring = list(ignoring)
    club_ids = {membership.club_id for membership in ignoring}
    club_seasons = {(membership.club_id, membership.season_id) for membership in ignoring}

    if ClubMembership.objects.filter(member=member).exclude(pk__in=[membership.pk for membership in ignoring]).exists():
        return False

    if ClubRole.objects.filter(member=member).exclude(club_id__in=club_ids, role=ClubRole.Roles.MEMBER).exists():
        return False

    family_rows = FamilyMembership.objects.filter(member=member)
    if family_rows.exclude(family__club_id__in=club_ids).exists():
        return False
    for family_row in family_rows.filter(role__in=[FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN]):
        if FamilyMembership.objects.filter(family_id=family_row.family_id).exclude(member=member).exists():
            return False

    this_season_spots = Q()
    for club_id, season_id in club_seasons:
        this_season_spots |= Q(team__club_id=club_id, season_id=season_id)
    for placements in (member.team_memberships.all(), member.staff_assignments.all()):
        if (placements.exclude(this_season_spots) if club_seasons else placements).exists():
            return False

    for accessor, club_lookup in _HISTORY_RELATIONS:
        rows = getattr(member, accessor).all()
        if club_lookup is not None and club_ids:
            rows = rows.exclude(**{f"{club_lookup}__in": club_ids})
        if rows.exists():
            return False

    return True
