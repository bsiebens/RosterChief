"""Coach roster endpoints (API.md §7.3): ``/teams/{team_id}/roster`` and friends.

Rules are the web coach screens': the candidate pool is ``teams.services.eligible_roster_members``,
an edit goes through ``mobile.forms.CoachRosterEditForm`` (so the jersey rules and their messages
are the same), and removing oneself as staff is refused. Reading is open to any staff of the team;
changes need a management position (``can_edit_roster``). Nothing here returns contact details
or birth dates (API.md §8); the candidates carry a birth *year* only.
"""

import uuid
from collections import defaultdict
from typing import Annotated, Literal

from django.db import transaction
from django.db.models import Case, CharField, Exists, IntegerField, OuterRef, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from api.core.errors import ApiError
from api.core.forms import form_error
from api.core.pagination import paginate_keyset
from club.models import ClubMembership, Season
from club.services.access import current_season
from members.models import Member
from mobile.forms import CoachRosterEditForm
from teams.models import Position, StaffAssignment, TeamMembership
from teams.services import eligible_roster_members

from ..coach import CoachScope, coach_scope, photo_url_for
from ..coach_schemas import AddPlayersIn, AddStaffIn, CandidateListOut, RosterOut, RosterPatch
from ..schemas import ErrorOut

router = Router(tags=["coach"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}


def _position_out(position) -> dict:
    return {"id": position.pk, "name": position.name, "short_name": position.short_name, "ordering": position.ordering, "management_position": position.management_position}


def roster_out(request, coach: CoachScope) -> dict:
    season = current_season(coach.club)
    positions = Position.objects.filter(club=coach.club)
    players, staff = [], []
    if season is not None:
        memberships = list(TeamMembership.objects.filter(team=coach.team, season=season).select_related("member", "position").order_by("position__ordering", "member__last_name", "member__first_name"))
        pending = set(ClubMembership.objects.filter(club=coach.club, season=season, member_id__in=[m.member_id for m in memberships], status=ClubMembership.StatusChoices.PENDING).values_list("member_id", flat=True))
        for membership in memberships:
            member = membership.member
            players.append(
                {
                    "membership_id": membership.pk,
                    "member_id": member.pk,
                    "first_name": member.first_name,
                    "last_name": member.last_name,
                    "position_id": membership.position_id,
                    "position": membership.position.name if membership.position_id else "",
                    "jersey_number": membership.jersey_number,
                    "is_captain": membership.is_captain,
                    "is_alternate_captain": membership.is_alternate_captain,
                    "sign_up_pending": member.pk in pending,
                    "photo_url": photo_url_for(request, member, coach.club),
                }
            )
        me = coach.club_scope.me
        for assignment in StaffAssignment.objects.filter(team=coach.team, season=season).select_related("member", "position").order_by("position__ordering", "member__last_name", "member__first_name"):
            member = assignment.member
            staff.append(
                {
                    "assignment_id": assignment.pk,
                    "member_id": member.pk,
                    "first_name": member.first_name,
                    "last_name": member.last_name,
                    "position_id": assignment.position_id,
                    "position": assignment.position.name,
                    "is_manager": assignment.position.management_position,
                    "is_me": me is not None and member.pk == me.pk,
                    "photo_url": photo_url_for(request, member, coach.club),
                }
            )
    return {
        "season_id": season.pk if season else None,
        "players": players,
        "staff": staff,
        "player_positions": [_position_out(position) for position in positions.filter(staff_position=False)],
        "staff_positions": [_position_out(position) for position in positions.filter(staff_position=True)],
    }


def _require_season(coach: CoachScope) -> Season:
    season = current_season(coach.club)
    if season is None:
        raise ApiError(409, "no_current_season", _("The club has no current season."))
    return season


@router.get("/teams/{team_id}/roster", response={200: RosterOut, **ERRORS}, summary="The team's players and staff")
def get_roster(request, team_id: uuid.UUID):
    """This season's roster and staff with the positions each may take. Any staff of the team."""
    coach = coach_scope(request, team_id)
    return roster_out(request, coach)


def _pool(coach: CoachScope, season, role: str):
    model = TeamMembership if role == "player" else StaffAssignment
    taken = model.objects.filter(team=coach.team, season=season).values_list("member_id", flat=True)
    return eligible_roster_members(coach.club).exclude(pk__in=taken)


def _age_signal(team, season):
    """``(match, adjacent)`` Q objects on Member.date_of_birth for this team's age range, or
    ``(None, None)`` when the team has none. Works on birth years: a team takes the birth years
    ``season year - age_max`` to ``season year - age_min`` (the year the season starts, so a U12 for
    2026-27 takes 2014-2015), a missing bound is open, and "adjacent" is the one birth year just
    outside a bound that is set."""
    if team.age_min is None and team.age_max is None:
        return None, None
    year = season.start_date.year
    oldest = year - team.age_max if team.age_max is not None else None  # the earliest birth year
    youngest = year - team.age_min if team.age_min is not None else None  # the latest birth year
    match = Q(date_of_birth__isnull=False)
    if oldest is not None:
        match &= Q(date_of_birth__year__gte=oldest)
    if youngest is not None:
        match &= Q(date_of_birth__year__lte=youngest)
    adjacent = Q()
    if oldest is not None:
        adjacent |= Q(date_of_birth__year=oldest - 1)
    if youngest is not None:
        adjacent |= Q(date_of_birth__year=youngest + 1)
    return match, Q(date_of_birth__isnull=False) & adjacent


@router.get("/teams/{team_id}/roster/candidates", response={200: CandidateListOut, **ERRORS}, summary="Who can be added")
def candidates(
    request,
    team_id: uuid.UUID,
    role: Annotated[Literal["player", "staff"], Query(description="Who to list: people not yet on the roster (`player`, default) or not yet on the staff (`staff`).")] = "player",
    filter: Annotated[
        Literal["all", "suggested", "no_team"] | None,
        Query(
            description="Players only. `suggested`: people with a `suggestion_reason`, best first (`age_match`, `previous_team`, `feeder_team`, `age_adjacent`), never anyone already on this team's roster. `no_team`: on no team's roster this season."
        ),
    ] = None,
    q: Annotated[str | None, Query(description="First or last name contains.")] = None,
    cursor: str | None = None,
    limit: int | None = None,
):
    """Active club members (not guardians) the team can take on. Sorted by last name, first name,
    id; `filter=suggested` sorts the best `suggestion_reason` first, then the same. Any staff of
    the team."""
    coach = coach_scope(request, team_id)
    season = current_season(coach.club)
    if season is None:
        return {"items": [], "next_cursor": None}
    pool = _pool(coach, season, role)
    on_a_roster = Exists(TeamMembership.objects.filter(member=OuterRef("pk"), season=season))
    members = Member.objects.filter(pk__in=pool.values("pk"))
    if role == "player":
        previous = Season.before(coach.club, season)
        match, adjacent = _age_signal(coach.team, season)
        signals = []  # (rank, reason, condition), best first
        if match is not None:
            signals.append((0, "age_match", match))
        if previous is not None:
            signals.append((1, "previous_team", Exists(TeamMembership.objects.filter(member=OuterRef("pk"), team=coach.team, season=previous))))
        signals.append((2, "feeder_team", Exists(TeamMembership.objects.filter(member=OuterRef("pk"), team__in=coach.team.feeder_teams.all(), season=season))))
        if adjacent is not None:
            signals.append((3, "age_adjacent", adjacent))
        members = members.annotate(
            suggestion_rank=Case(*[When(condition, then=Value(rank)) for rank, _reason, condition in signals], default=Value(4), output_field=IntegerField()),
            suggestion_reason=Case(*[When(condition, then=Value(reason)) for _rank, reason, condition in signals], default=Value(None), output_field=CharField()),
        )
        if filter == "no_team":
            members = members.filter(~on_a_roster)
        elif filter == "suggested":
            members = members.filter(suggestion_rank__lt=4)
    if q and q.strip():
        members = members.filter(Q(first_name__icontains=q.strip()) | Q(last_name__icontains=q.strip()))
    ordering = ["suggestion_rank", "last_name", "first_name", "pk"] if role == "player" and filter == "suggested" else ["last_name", "first_name", "pk"]
    page = paginate_keyset(members, fields=ordering, cursor=cursor, limit=limit)

    teams_by_member = defaultdict(list)
    memberships = TeamMembership.objects.filter(season=season, member_id__in=[member.pk for member in page.items]).select_related("team").order_by("team__name", "team_id")
    for membership in memberships:
        teams_by_member[membership.member_id].append({"id": membership.team_id, "name": membership.team.name})
    return {
        "items": [
            {
                "member_id": member.pk,
                "first_name": member.first_name,
                "last_name": member.last_name,
                "photo_url": photo_url_for(request, member, coach.club),
                "suggestion_reason": getattr(member, "suggestion_reason", None),
                "birth_year": member.date_of_birth.year if member.date_of_birth else None,
                "current_teams": teams_by_member[member.pk],
            }
            for member in page.items
        ],
        "next_cursor": page.next_cursor,
    }


@router.post("/teams/{team_id}/roster/players", response={200: RosterOut, **ERRORS, **INVALID, 409: ErrorOut}, summary="Add players to the roster")
def add_players(request, team_id: uuid.UUID, payload: AddPlayersIn):
    """They start without a position or number. Anyone already on the roster is left alone, so a
    retry is harmless; anyone who is not a candidate fails the whole call with `422
    not_a_candidate` (`fields.member_ids`) and nothing is added. Adding a player invites them to
    the team's upcoming events (they get one "New events on your calendar" notification). Answers
    the roster. Managers only."""
    coach = coach_scope(request, team_id, manager=True)
    season = _require_season(coach)
    wanted = set(payload.member_ids)
    with transaction.atomic():
        on_roster = set(TeamMembership.objects.filter(team=coach.team, season=season, member_id__in=wanted).values_list("member_id", flat=True))
        allowed = set(_pool(coach, season, "player").filter(pk__in=wanted - on_roster).values_list("pk", flat=True))
        invalid = wanted - on_roster - allowed
        if invalid:
            raise ApiError(422, "not_a_candidate", _("Some of these people cannot be added to the roster."), fields={"member_ids": sorted(map(str, invalid))})
        for member_id in allowed:
            TeamMembership.objects.get_or_create(team=coach.team, season=season, member_id=member_id)
    return roster_out(request, coach)


@router.post("/teams/{team_id}/roster/staff", response={200: RosterOut, **ERRORS, **INVALID, 409: ErrorOut}, summary="Add staff")
def add_staff(request, team_id: uuid.UUID, payload: AddStaffIn):
    """All get the same staff `position_id` (add them in several calls for different roles). Same
    rules as adding players: staff already on the team are left alone, anyone else who is not a
    candidate fails the call (`422 not_a_candidate`). Answers the roster. Managers only."""
    coach = coach_scope(request, team_id, manager=True)
    season = _require_season(coach)
    position = Position.objects.filter(club=coach.club, staff_position=True, pk=payload.position_id).first()
    if position is None:
        raise ApiError(422, "validation_error", fields={"position_id": [str(_("Pick a staff position."))]})
    wanted = set(payload.member_ids)
    with transaction.atomic():
        on_staff = set(StaffAssignment.objects.filter(team=coach.team, season=season, member_id__in=wanted).values_list("member_id", flat=True))
        allowed = set(_pool(coach, season, "staff").filter(pk__in=wanted - on_staff).values_list("pk", flat=True))
        invalid = wanted - on_staff - allowed
        if invalid:
            raise ApiError(422, "not_a_candidate", _("Some of these people cannot be added to the staff."), fields={"member_ids": sorted(map(str, invalid))})
        for member_id in allowed:
            StaffAssignment.objects.get_or_create(team=coach.team, season=season, member_id=member_id, defaults={"position": position})
    return roster_out(request, coach)


def _membership(coach: CoachScope, membership_id) -> TeamMembership:
    """A player of this team this season (a past season's row is outside the coach's current role)."""
    season = current_season(coach.club)
    return get_object_or_404(TeamMembership.objects.select_related("member", "position"), pk=membership_id, team=coach.team, season=season)


@router.patch("/teams/{team_id}/roster/{membership_id}", response={200: RosterOut, **ERRORS, **INVALID}, summary="Change a player's position, number or captaincy")
def update_player(request, team_id: uuid.UUID, membership_id: uuid.UUID, payload: RosterPatch):
    """Only what is sent changes. Jersey rules are the web's: a number already taken by another
    player (or, for a team with a number pool, taken in the pool this season) is `422` with
    `fields.jersey_number`. A player must keep a position (`fields.position_id`). Answers the
    roster. Managers only."""
    coach = coach_scope(request, team_id, manager=True)
    membership = _membership(coach, membership_id)
    data = payload.model_dump(exclude_unset=True)
    values = {"position_id": membership.position_id, "jersey_number": membership.jersey_number, "is_captain": membership.is_captain, "is_alternate_captain": membership.is_alternate_captain, **data}
    post = {}
    if values["position_id"] is not None:
        post["position"] = str(values["position_id"])
    if values["jersey_number"] is not None:
        post["jersey_number"] = values["jersey_number"]
    for flag in ("is_captain", "is_alternate_captain"):
        if values[flag]:
            post[flag] = "on"
    form = CoachRosterEditForm(post, instance=membership, club=coach.club, team=coach.team, season=membership.season)
    if not form.is_valid():
        error = form_error(form)
        error.fields = {("position_id" if name == "position" else name): messages for name, messages in error.fields.items()}
        raise error
    form.save()
    return roster_out(request, coach)


@router.delete("/teams/{team_id}/roster/{membership_id}", response={204: None, **ERRORS}, summary="Remove a player from the roster")
def remove_player(request, team_id: uuid.UUID, membership_id: uuid.UUID):
    """Removes this season's roster spot; the person stays a club member. Managers only."""
    coach = coach_scope(request, team_id, manager=True)
    _membership(coach, membership_id).delete()
    return 204, None


@router.delete("/teams/{team_id}/staff/{assignment_id}", response={204: None, **ERRORS}, summary="Remove a staff member")
def remove_staff(request, team_id: uuid.UUID, assignment_id: uuid.UUID):
    """Managers only. Removing **yourself** is `403 self_removal`: it would lock you out of the team
    you are looking at; another manager or an admin does that on the web."""
    coach = coach_scope(request, team_id, manager=True)
    assignment = get_object_or_404(StaffAssignment, pk=assignment_id, team=coach.team, season=current_season(coach.club))
    me = coach.club_scope.me
    if me is not None and assignment.member_id == me.pk:
        raise ApiError(403, "self_removal", _("You can't remove yourself from the staff of a team you manage."))
    assignment.delete()
    return 204, None
