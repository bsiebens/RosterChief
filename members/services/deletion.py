"""When a Member row may be hard-deleted, and what a club may do to one.

Member is the one person record shared by every club (it carries no club FK
of its own), so deleting it cascades through everything hanging off it
anywhere on the platform -- other clubs' memberships, roles, families,
rosters, attendance, evaluations. Every code path that wants to remove a
Member outright (cancelling a one-off sign-up, merging away a duplicate)
asks ``member_is_deletable`` first and falls back to removing only its own
club's rows when the answer is no.

The same reasoning limits what one club may change: while
``member_has_ties_outside_club`` is true (a login, or anything in another
club), the person's own details are read-only to the club and removing them
from the club goes through ``remove_member_from_club`` rather than deleting
the record.
"""

from django.db import transaction
from django.db.models import Q

from club.models import ClubMembership, ClubRole
from members.models import Family, FamilyMembership

#: Reverse relations that carry real history or grants, each paired with the
#: lookup from that row to its club, so rows belonging to the club whose
#: memberships are being removed can be set aside (they cascade with the
#: member, same as the membership itself).
_CLUB_HISTORY_RELATIONS = (
    ("shop_manager_grants", "club"),
    ("referee_manager_grants", "club"),
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
)

#: The PROTECT-ed, money-related shop relations, with their club lookup.
_SHOP_RELATIONS = (
    ("orders", "club"),
    ("order_items", "order__club"),
    ("cart_items", "cart__club"),
    ("vouchers", "club"),
    ("applied_discounts", "order__club"),
)

#: What member_is_deletable checks: ``None`` as the lookup means "any row at
#: all keeps the member" -- shop records are never set aside.
_HISTORY_RELATIONS = (*_CLUB_HISTORY_RELATIONS, *((accessor, None) for accessor, _lookup in _SHOP_RELATIONS))


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


#: Every reverse relation from Member to a club-owned row, paired with the
#: lookup from that row to its club. Used both to answer "does this person
#: have anything outside this club?" and to remove a person from one club
#: without touching any other. Shop records are PROTECT-ed (never removed
#: here) but still count as ties; SET_NULL relations (authorship of events,
#: news, form sends, ...) are neither ties nor removed.
_CLUB_RELATIONS = (
    ("member_of", "club"),
    ("roles", "club"),
    ("family_memberships", "family__club"),
    ("team_memberships", "team__club"),
    ("staff_assignments", "team__club"),
    ("event_task_claims", "task__event__club"),
    ("notifications", "club"),
    ("push_subscriptions", "club"),
    *_CLUB_HISTORY_RELATIONS,
)

#: Many-to-many audience lists on club-owned rows (who an event or form send
#: is addressed to). Removing a person from a club takes them off this club's
#: lists; they are not ties on their own.
_CLUB_AUDIENCES = (
    ("invited_to_events", "club"),
    ("excluded_from_events", "club"),
    ("invited_to_event_series", "club"),
    ("excluded_from_event_series", "club"),
    ("invited_to_form_sends", "club"),
    ("excluded_from_form_sends", "club"),
)


def member_has_ties_outside_club(member, club) -> bool:
    """Whether ``member`` matters to anyone other than ``club``: they have a
    login (``member.user`` -- the account holder owns their own identity), or
    any row that belongs to another club (a membership, role, grant, family
    row, roster/staff spot, attendance, evaluation, shop record, ...).

    Member is global, so while this is True a club must not change the
    person's own details (name, date of birth, contact fields, photo) nor
    delete the record -- it only manages its own rows about them
    (management.views.MemberUpdateView/MemberDeleteView/MemberGrantLoginView).
    When it is False the member is effectively this club's alone, and
    deleting them only removes this club's data."""
    if member.pk is None:
        return False
    if member.user_id is not None:
        return True
    for accessor, club_lookup in (*_CLUB_RELATIONS, *_SHOP_RELATIONS):
        if getattr(member, accessor).exclude(**{club_lookup: club}).exists():
            return True
    return False


@transaction.atomic
def remove_member_from_club(member, club):
    """Remove everything ``club`` holds about ``member`` -- what deleting the
    member would have removed at this club -- while leaving the Member row and
    anything in any other club untouched. Used instead of ``member.delete()``
    when member_has_ties_outside_club says the record isn't this club's alone.

    Shop records (PROTECT) stay, as do rows that only name the member as an
    author. A family of this club that is left empty is deleted; a family of
    any other club never is."""
    family_ids = list(member.family_memberships.filter(family__club=club).values_list("family_id", flat=True))
    for accessor, club_lookup in _CLUB_RELATIONS:
        getattr(member, accessor).filter(**{club_lookup: club}).delete()
    for accessor, club_lookup in _CLUB_AUDIENCES:
        related = getattr(member, accessor)
        related.remove(*related.filter(**{club_lookup: club}))
    Family.objects.filter(pk__in=family_ids, club=club, memberships__isnull=True).delete()
