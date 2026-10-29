"""The management member list's filtering, shared by the /manage page and the panel API.

Both surfaces take the same filters and must show the same people, so the query lives here
once. Every filter keys off the *current* season's ClubMembership: a member's status or fee
last season isn't what "filter by Pending" means on a page showing everyone's standing now.
"""

from django.db.models import Q

from club.models import ClubMembership
from club.services.access import members_visible_to
from club.services.onboarding import members_with_open_requirements
from controlpanel.services.statistics import unrostered_members

KINDS = ("member", "guardian", "staff", "both")


def normalize_kind(kind):
    """Unknown values fall back to the default ``member``."""
    return kind if kind in KINDS else "member"


def filter_members(user, club, season, *, kind="member", status="", fee_status="", team_id="", unrostered=False, open_documents=False, q=""):
    """The members ``user`` may see in ``club``, narrowed by the list filters.

    ``kind``: ``member`` (default) hides guardians and staff, ``guardian``/``staff`` show only
    those tagged so on the season's membership, ``both`` shows everyone. Always intersected with
    ``members_visible_to`` so a non-admin only sees people already visible to them.
    ``status``: a membership status of ``season``, ``all``, or empty (hides lapsed/cancelled).
    """
    kind = normalize_kind(kind)
    if kind in ("guardian", "staff"):
        tagged = ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind(kind))
        members = members_visible_to(user, club, include_guardians=True).filter(pk__in=tagged.values("member_id"))
    elif kind == "both":
        members = members_visible_to(user, club, include_guardians=True)
    else:
        members = members_visible_to(user, club)

    if status == "all":
        pass
    elif status:
        members = members.filter(member_of__season=season, member_of__status=status)
    else:
        # A pk__in subquery rather than .exclude() across the to-many relation; members with no
        # current membership at all stay visible.
        ended = ClubMembership.objects.filter(club=club, season=season, status__in=[ClubMembership.StatusChoices.LAPSED, ClubMembership.StatusChoices.CANCELLED])
        members = members.exclude(pk__in=ended.values("member_id"))
    if fee_status:
        members = members.filter(member_of__season=season, member_of__fee_status=fee_status)
    if team_id:
        members = members.filter(team_memberships__season=season, team_memberships__team_id=team_id, team_memberships__team__club=club)
    if unrostered:
        # Same query club_attention() counts for the dashboard's "Unrostered members" row.
        members = members.filter(pk__in=unrostered_members(club, season).values("pk"))
    if open_documents:
        members = members.filter(pk__in=members_with_open_requirements(club, season).values("pk"))

    term = (q or "").strip()
    if term:
        members = members.filter(Q(first_name__icontains=term) | Q(last_name__icontains=term) | Q(email__icontains=term) | Q(user__email__icontains=term))
    return members.distinct()
