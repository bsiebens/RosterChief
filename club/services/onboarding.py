"""Per-member onboarding checklist -- see OnboardingRequirement's docstring
(club/models.py) for why fee_status stays untouched by any of this, and for
why approve_one/approve_all_clean below are the only way to reach
ClubMembership.status ACTIVE (fee_status alone, even fully PAID, never does).

No signal pre-creates a MemberRequirementStatus row when a membership is created
or a requirement is added: "required, no row yet" and "required, row with
is_complete=is_bypassed=False" both mean the same thing (not done), so there is
nothing to backfill either way -- a club adding a new requirement mid-season
immediately shows it as open on every existing membership, and deactivating one
immediately stops asking for it, with no migration-shaped cleanup step in either
direction.
"""

from collections import defaultdict

from django.db.models import Q
from django.utils import timezone

from club.models import ClubMembership, MemberRequirementStatus, OnboardingRequirement
from members.models import Member

#: Shared by every "is this item resolved" check below -- resolved means it no
#: longer blocks anything, whether that's because it was actually completed or
#: because staff decided it doesn't apply to this person.
_RESOLVED = Q(is_complete=True) | Q(is_bypassed=True)


def checklist_for(membership):
    """Every active requirement for this membership's club, each paired with its
    status row if one exists (or None -- not started). One query for the
    requirements, one for the statuses that exist; the membership detail page
    renders exactly this list under its Documents tab."""
    requirements = OnboardingRequirement.objects.filter(club_id=membership.club_id, is_active=True)
    statuses = {status.requirement_id: status for status in membership.requirement_statuses.select_related("completed_by")}

    return [(requirement, statuses.get(requirement.pk)) for requirement in requirements]


def mark_complete(membership, requirement, *, user, document=None, note=""):
    """Actually received/verified -- as opposed to mark_bypassed, "not needed for
    this person". Clears any prior bypass: the two are mutually exclusive."""
    status, _created = MemberRequirementStatus.objects.get_or_create(membership=membership, requirement=requirement)
    status.is_complete = True
    status.is_bypassed = False
    status.completed_at = timezone.now()
    status.completed_by = user
    status.note = note
    if document:
        status.document = document
    status.save()

    return status


def mark_bypassed(membership, requirement, *, user, note=""):
    """Confirmed not needed for this member (e.g. they already have a recent
    photo on file) -- stops the item blocking anything, same as mark_complete,
    but reads correctly on the checklist/audit trail as a deliberate staff
    decision rather than a document actually received. A note is expected here
    (not enforced at this layer -- see RequirementBypassForm) since "why" is the
    whole point of a bypass in a way it isn't for an ordinary completion."""
    status, _created = MemberRequirementStatus.objects.get_or_create(membership=membership, requirement=requirement)
    status.is_complete = False
    status.is_bypassed = True
    status.completed_at = timezone.now()
    status.completed_by = user
    status.note = note
    status.document = None
    status.save()

    return status


def mark_incomplete(membership, requirement):
    """Undo a mark_complete/mark_bypassed -- kept as a row (not deleted) so the
    document/note a club already collected isn't thrown away by an accidental
    toggle."""
    status, _created = MemberRequirementStatus.objects.get_or_create(membership=membership, requirement=requirement)
    status.is_complete = False
    status.is_bypassed = False
    status.completed_at = None
    status.completed_by = None
    status.save()

    return status


def annotate_onboarding_status(queryset):
    """`queryset` of ClubMembership, returned as a list with each row given an
    `.onboarding_open` attribute (count of unresolved active requirements) -- the
    list-page equivalent of the `open_requirement_count` property, in a fixed
    number of queries regardless of list size rather than the N+1 a per-row
    property call would cost across a whole table."""
    memberships = list(queryset)
    if not memberships:
        return memberships

    required_by_club = {}
    for club_id in {membership.club_id for membership in memberships}:
        required_by_club[club_id] = set(OnboardingRequirement.objects.filter(club_id=club_id, is_active=True).values_list("pk", flat=True))

    met_by_membership = defaultdict(set)
    statuses = MemberRequirementStatus.objects.filter(membership_id__in=[membership.pk for membership in memberships]).filter(_RESOLVED)
    for membership_id, requirement_id in statuses.values_list("membership_id", "requirement_id"):
        met_by_membership[membership_id].add(requirement_id)

    for membership in memberships:
        required = required_by_club.get(membership.club_id, set())
        membership.onboarding_open = len(required - met_by_membership[membership.pk])

    return memberships


def members_with_open_requirements(club, season):
    """Members whose current-season membership has at least one unresolved active
    requirement -- the same condition the dashboard's "Missing documentation" KPI
    counts (management.views.HomeView), reused here for the member list's own
    ?docs=open filter. None when there's no season to check against."""
    if season is None:
        return Member.objects.none()

    memberships = list(ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER))
    annotate_onboarding_status(memberships)
    member_ids = [membership.member_id for membership in memberships if membership.onboarding_open]
    return Member.objects.filter(pk__in=member_ids)


def blocking_event_kinds(membership) -> set:
    """Every event kind currently blocked for this membership by at least one open
    (not complete, not bypassed) active requirement -- e.g. {"game"} while a medical
    certificate is outstanding but nothing blocks training. Powers the Sign-up page's
    detail pane and member_detail's Documents tab ("blocks: Games" next to an open
    item), so staff can see exactly what's at stake without reading every requirement."""
    blocked = set()
    for requirement, status in checklist_for(membership):
        if status is not None and (status.is_complete or status.is_bypassed):
            continue
        blocked.update(requirement.blocked_event_kinds)
    return blocked


def blocked_member_ids_for_event(club, season, event_kind) -> set:
    """Member ids that must NOT be invited to (or selectable for) an event of
    `event_kind` this season, because at least one active requirement that blocks
    that kind is still open on their current-season membership. Bulk, not per-member
    -- events.services.attendance.effective_members() calls this once per event save,
    not once per candidate member.

    A member with no current-season ClubMembership.MEMBER row at all isn't covered
    here -- effective_members() already wouldn't include them (they're not on any
    roster to begin with), so there's nothing to subtract.

    Filtered in Python, not via a `blocked_event_kinds__contains=[event_kind]`
    queryset lookup -- JSONField `contains` isn't supported on SQLite (only
    Postgres/MySQL/Oracle), and a club's own requirement count is always small
    enough that fetching them all costs nothing worth optimising away."""
    blocking_requirement_ids = {requirement.pk for requirement in OnboardingRequirement.objects.filter(club=club, is_active=True) if event_kind in requirement.blocked_event_kinds}
    if not blocking_requirement_ids:
        return set()

    memberships = ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER)
    resolved_by_membership = defaultdict(set)
    statuses = MemberRequirementStatus.objects.filter(membership__in=memberships, requirement_id__in=blocking_requirement_ids).filter(_RESOLVED)
    for membership_id, requirement_id in statuses.values_list("membership_id", "requirement_id"):
        resolved_by_membership[membership_id].add(requirement_id)

    blocked_member_ids = set()
    for membership_id, member_id in memberships.values_list("pk", "member_id"):
        if blocking_requirement_ids - resolved_by_membership.get(membership_id, set()):
            blocked_member_ids.add(member_id)
    return blocked_member_ids


#: Fee states "clean" enough to activate on -- PARTIALLY_PAID/UNPAID never are.
_CLEAN_FEE_STATUSES = (ClubMembership.FeeStatus.PAID, ClubMembership.FeeStatus.WAIVED)


def is_signup_clean(membership) -> bool:
    """Paid up (or waived) and every active requirement resolved -- what both
    approve_all_clean and approve_one gate on, and what the Sign-up page's
    per-member Approve button enables/disables against. Not itself a shortcut
    for "already active": a membership can be exactly this clean and still be
    PENDING, waiting on this deliberately manual step."""
    return membership.fee_status in _CLEAN_FEE_STATUSES and membership.onboarding_complete


def approve_one(membership) -> bool:
    """Admin-triggered single activation from the Sign-up page's detail panel --
    same rule and same reasoning as approve_all_clean, just one membership instead
    of a whole season's queue. Returns whether it actually activated (False if it
    wasn't PENDING or wasn't clean)."""
    if membership.status != ClubMembership.StatusChoices.PENDING or not is_signup_clean(membership):
        return False
    membership.status = ClubMembership.StatusChoices.ACTIVE
    update_fields = ["status"]
    if membership.activated_at is None:
        membership.activated_at = timezone.localdate()
        update_fields.append("activated_at")
    membership.save(update_fields=update_fields)
    return True


def approve_all_clean(club, season) -> int:
    """Admin-triggered bulk activation from the Sign-up page -- the *only* path to
    ClubMembership.status ACTIVE (see OnboardingRequirement's docstring: paying in
    full only settles fee_status now, club.services.fees._sync_fee_status never
    touches status). Only ever moves PENDING -> ACTIVE, and only for a membership
    that is both paid up (fee_status PAID or WAIVED) and has resolved every active
    requirement -- "manual documentation check to be done by the admin" means
    clicking this once everything has actually been checked, not something that runs
    on its own. Returns how many memberships were activated."""
    memberships = list(
        ClubMembership.objects.filter(
            club=club,
            season=season,
            kind=ClubMembership.Kind.MEMBER,
            status=ClubMembership.StatusChoices.PENDING,
            fee_status__in=_CLEAN_FEE_STATUSES,
        )
    )
    annotate_onboarding_status(memberships)
    ready = [membership for membership in memberships if membership.onboarding_open == 0]
    today = timezone.localdate()
    for membership in ready:
        membership.status = ClubMembership.StatusChoices.ACTIVE
        if membership.activated_at is None:
            membership.activated_at = today
    if ready:
        ClubMembership.objects.bulk_update(ready, ["status", "activated_at"])
    return len(ready)
