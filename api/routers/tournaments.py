"""``/tournaments``: tournaments, their registration queue and teams. Reading needs either tournament permission; settings, the queue
and payments need ``tournaments.manage_tournaments``; entering a team by hand needs ``tournaments.operate_tournaments`` (the manage
tier includes it). Permission (403) is checked before the ``tournaments`` feature (404). Rules live in ``tournaments.services``."""

import uuid

from django.db import transaction
from django.db.models import Count, Q
from ninja import File, Router, UploadedFile

from api.auth import AUTHENTICATORS
from api.routers import tournaments_common as c
from api.schemas_tournaments import ManualTeamIn, PaymentIn, RejectIn, TournamentIn, TournamentPatch, WithdrawIn
from events.models import Season
from formbuilder.services.management import Conflict
from members.services import access
from members.services.errors import Invalid
from teams.models import Team
from tournaments.models import Tournament, TournamentRegistration, TournamentTeam
from tournaments.services import notifications
from tournaments.services import registration as svc

router = Router(auth=AUTHENTICATORS)

SETTINGS = (
    "name", "status", "start_date", "end_date", "registration_open", "max_teams", "earliest_birth_year", "latest_birth_year", "contact_email", "description", "deposit_amount", "entry_fee_amount",
    "lunch_mode", "lunch_price_per_day", "lunch_billed_with_fee", "default_match_duration_minutes",
)  # fmt: skip


def _money(value):
    return None if value is None else f"{value:.2f}"


def tournament_item(t: Tournament) -> dict:
    return {
        "id": t.pk, "slug": t.slug, "name": t.name, "season": c.ref(t.season), "start_date": t.start_date, "end_date": t.end_date, "status": t.status,
        "registration_open": t.registration_open, "max_teams": t.max_teams, "accepts_registrations": t.accepts_registrations,
        "pending_registration_count": getattr(t, "pending_registration_count", None), "teams_in_count": getattr(t, "teams_in_count", None),
    }  # fmt: skip


def tournament_detail(t: Tournament, user) -> dict:
    location = t.location
    return {
        **tournament_item(t),
        "contact_email": t.contact_email, "description": t.description, "earliest_birth_year": t.earliest_birth_year, "latest_birth_year": t.latest_birth_year, "home_team": c.ref(t.home_team), "event_id": t.event_id,
        "deposit_amount": _money(t.deposit_amount), "entry_fee_amount": _money(t.entry_fee_amount), "lunch_mode": t.lunch_mode, "lunch_price_per_day": _money(t.lunch_price_per_day),
        "lunch_billed_with_fee": t.lunch_billed_with_fee, "default_match_duration_minutes": t.default_match_duration_minutes, "schedule_needs_regeneration": t.schedule_needs_regeneration,
        "dates": t.date_range(), "location": c.ref(location),
        "pending_registration_count": t.registrations.filter(status=TournamentRegistration.Status.PENDING).count(),
        "teams_in_count": t.teams.filter(withdrawn_at__isnull=True).count(),
        "can_manage": access.can_manage_tournaments(user), "can_operate": access.can_operate_tournaments(user),
    }  # fmt: skip


def _season_for(start, end):
    season = Season.objects.filter(start_date__lte=start, end_date__gte=end).first()
    if season is None:
        raise Invalid("No season covers that date range.", {"start_date": ["No season of this club covers that date range. Add a season that includes these dates first."]})
    return season


def _home_team(team_id):
    if team_id is None:
        return None
    team = Team.objects.filter(pk=team_id).first()
    if team is None:
        raise Invalid("Unknown team.", {"home_team_id": ["Unknown team."]})
    return team


def _check_dates(t: Tournament):
    if t.end_date < t.start_date:
        raise Invalid("Must be on or after the start date.", {"end_date": ["Must be on or after the start date."]})
    t.season = _season_for(t.start_date, t.end_date)


def _after_save(t: Tournament, request, *, was_published: bool):
    svc.ensure_home_team(t)
    if not was_published and t.status == Tournament.Status.PUBLISHED:
        notifications.dispatch_notify_tournament_shifts_open(str(t.pk))


@router.get("", summary="Tournaments of the club", operation_id="tournaments_list")
def list_tournaments(request, season: uuid.UUID | None = None, status: str | None = None):
    c.gate_view(request)
    qs = Tournament.objects.select_related("season").annotate(
        pending_registration_count=Count("registrations", filter=Q(registrations__status=TournamentRegistration.Status.PENDING), distinct=True),
        teams_in_count=Count("teams", filter=Q(teams__withdrawn_at__isnull=True), distinct=True),
    )  # fmt: skip
    if season is not None:
        qs = qs.filter(season_id=season)
    if status:
        qs = qs.filter(status=status)
    seasons = [c.ref(s) for s in Season.objects.order_by("-start_date")]
    return {"results": [tournament_item(t) for t in qs], "seasons": seasons, "can_manage": access.can_manage_tournaments(request.user)}


@router.post("", summary="Create a tournament", operation_id="tournaments_create", response={201: dict})
def create_tournament(request, payload: TournamentIn):
    c.gate_manage(request)
    data = payload.dict()
    home = _home_team(data.pop("home_team_id"))
    t = Tournament(home_team=home, **data)
    _check_dates(t)
    c.validate(t, exclude=["season"])
    t.save()
    _after_save(t, request, was_published=False)
    return 201, tournament_detail(t, request.user)


@router.get("/{tournament_id}", summary="One tournament with its settings", operation_id="tournaments_detail")
def get_tournament(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    return tournament_detail(c.get_tournament(tournament_id), request.user)


@router.patch("/{tournament_id}", summary="Change a tournament's settings", operation_id="tournaments_update")
def update_tournament(request, tournament_id: uuid.UUID, payload: TournamentPatch):
    c.gate_manage(request)
    t = c.get_tournament(tournament_id)
    was_published = t.status == Tournament.Status.PUBLISHED
    data = payload.dict(exclude_unset=True)
    if "home_team_id" in data:
        t.home_team = _home_team(data.pop("home_team_id"))
    c.apply(t, {k: v for k, v in data.items() if k in SETTINGS})
    _check_dates(t)
    c.validate(t, exclude=["season"])
    t.save()
    _after_save(t, request, was_published=was_published)
    return tournament_detail(c.get_tournament(t.pk), request.user)


@router.delete("/{tournament_id}", summary="Delete a draft tournament (and the calendar event it created)", operation_id="tournaments_delete", response={204: None})
def delete_tournament(request, tournament_id: uuid.UUID):
    c.gate_manage(request)
    t = c.get_tournament(tournament_id)
    if t.status != Tournament.Status.DRAFT:
        raise Conflict("Only a draft tournament can be deleted.", "not_draft")
    with transaction.atomic():
        event = t.event  # always the one ensure_home_team created: the API never links an event by hand
        t.delete()
        if event is not None:
            event.delete()
    return 204, None


# --- registration queue -----------------------------------------------------------------------------------------------------

_GROUP_ORDER = {TournamentRegistration.Status.APPROVED: 0, TournamentRegistration.Status.PENDING: 1, TournamentRegistration.Status.REJECTED: 2}


def registration_out(r: TournamentRegistration, tournament: Tournament, *, oldest_pending=False) -> dict:
    team = getattr(r, "tournament_team", None)
    return {
        "id": r.pk, "status": r.status, "name": r.display_name, "club_team": c.ref(r.team), "is_foreign": r.is_foreign, "contact_name": r.foreign_contact_name,
        "contact_email": r.foreign_contact_email, "contact_phone": r.foreign_contact_phone, "primary_color": r.foreign_primary_color, "secondary_color": r.foreign_secondary_color,
        "logo_url": c.url(r.foreign_logo), "rejection_reason": r.rejection_reason, "submitted_at": r.submitted_at, "reviewed_at": r.reviewed_at,
        "tournament_team_id": team.pk if team is not None else None, "is_oldest_pending": oldest_pending,
        "players": [c.person_out(p, tournament) for p in r.players.all()],
    }  # fmt: skip


def _registration(registration_id) -> TournamentRegistration:
    return c.get_or_404(TournamentRegistration.objects.select_related("tournament", "team"), pk=registration_id)


@router.get("/{tournament_id}/registrations", summary="The registration queue: approved, then pending (oldest first), then rejected", operation_id="tournaments_registrations")
def registrations(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    t = c.get_tournament(tournament_id)
    rows = list(TournamentRegistration.objects.filter(tournament=t).select_related("team", "tournament_team").prefetch_related("players"))
    rows.sort(key=lambda r: (_GROUP_ORDER[r.status], r.submitted_at))
    oldest = next((r.pk for r in rows if r.status == TournamentRegistration.Status.PENDING), None)
    return {"results": [registration_out(r, t, oldest_pending=r.pk == oldest) for r in rows]}


@router.post("/registrations/{registration_id}/approve", summary="Approve a pending registration: the team joins the tournament", operation_id="tournaments_registration_approve")
def approve(request, registration_id: uuid.UUID):
    c.gate_manage(request)
    r = _registration(registration_id)
    try:
        team = svc.approve_registration(r, reviewed_by=request.user)
    except svc.RegistrationDecisionError as error:
        raise Conflict(str(error), "already_decided") from error
    r = _registration(registration_id)
    notifications.send_registration_decision_email(r, request=request)
    return {**registration_out(r, r.tournament), "tournament_team_id": team.pk}


@router.post("/registrations/{registration_id}/reject", summary="Reject a pending registration", operation_id="tournaments_registration_reject")
def reject(request, registration_id: uuid.UUID, payload: RejectIn):
    c.gate_manage(request)
    r = _registration(registration_id)
    try:
        svc.reject_registration(r, reviewed_by=request.user, reason=payload.reason)
    except svc.RegistrationDecisionError as error:
        raise Conflict(str(error), "already_decided") from error
    r = _registration(registration_id)
    notifications.send_registration_decision_email(r, request=request)
    return registration_out(r, r.tournament)


# --- teams ------------------------------------------------------------------------------------------------------------------


def _team(team_id) -> TournamentTeam:
    return c.get_or_404(TournamentTeam.objects.select_related("team", "tournament"), pk=team_id)


@router.get("/{tournament_id}/teams", summary="Every team in the tournament (withdrawn ones included)", operation_id="tournaments_teams")
def teams(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    t = c.get_tournament(tournament_id)
    rows = list(t.teams.select_related("team").prefetch_related("roster_entries"))
    for row in rows:
        row.tournament = t
    return {"results": [c.team_out(row, t) for row in rows], "payment_statuses": [v for v, _label in TournamentTeam.PaymentStatus.choices]}


@router.post("/{tournament_id}/teams", summary="Enter a team by hand, bypassing the registration queue", operation_id="tournaments_team_create", response={201: dict})
def create_team(request, tournament_id: uuid.UUID, payload: ManualTeamIn):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    if payload.kind == "club":
        team = Team.objects.filter(pk=payload.team_id).first() if payload.team_id else None
        if team is None:
            raise Invalid("Choose a team.", {"team_id": ["Choose one of the club's teams."]})
        if TournamentTeam.objects.filter(tournament=t, team=team).exists():
            raise Invalid("Already entered.", {"team_id": ["This team is already in the tournament."]})
        created = svc.create_manual_team(t, club_team=team)
    else:
        errors = {f: ["This field is required."] for f in ("foreign_name", "foreign_contact_name", "foreign_contact_email") if not getattr(payload, f).strip()}
        if errors:
            raise Invalid("Missing details.", errors)
        created = svc.create_manual_team(
            t,
            foreign_fields={
                "foreign_name": payload.foreign_name.strip(), "foreign_contact_name": payload.foreign_contact_name.strip(), "foreign_contact_email": payload.foreign_contact_email.strip(),
                "foreign_contact_phone": payload.foreign_contact_phone, "foreign_primary_color": payload.foreign_primary_color, "foreign_secondary_color": payload.foreign_secondary_color,
            },
        )  # fmt: skip
    created = _team(created.pk)
    return 201, c.team_out(created, t)


@router.post("/teams/{team_id}/logo", summary="Upload a foreign team's logo", operation_id="tournaments_team_logo")
def team_logo(request, team_id: uuid.UUID, logo: File[UploadedFile]):
    c.gate_operate(request)
    team = _team(team_id)
    if team.team_id is not None:
        raise Invalid("Only a foreign team has a logo of its own.", {"logo": ["Only a foreign team has a logo of its own."]})
    if not (logo.content_type or "").startswith("image/"):
        raise Invalid("Not an image.", {"logo": ["Upload an image."]})
    team.foreign_logo.save(logo.name, logo, save=True)
    return c.team_out(team, team.tournament)


@router.patch("/teams/{team_id}/payment", summary="Set a team's payment status", operation_id="tournaments_team_payment")
def team_payment(request, team_id: uuid.UUID, payload: PaymentIn):
    c.gate_manage(request)
    team = _team(team_id)
    team.payment_status = payload.payment_status
    team.save(update_fields=["payment_status"])
    return c.team_out(team, team.tournament)


@router.post("/teams/{team_id}/withdraw", summary="Withdraw an approved team (soft: matches and roster stay)", operation_id="tournaments_team_withdraw")
def team_withdraw(request, team_id: uuid.UUID, payload: WithdrawIn):
    c.gate_manage(request)
    team = _team(team_id)
    try:
        svc.withdraw_team(team, reason=payload.reason, withdrawn_by=request.user)
    except svc.TeamWithdrawalError as error:
        raise Conflict(str(error), "cannot_withdraw") from error
    team = _team(team_id)
    return c.team_out(team, team.tournament)


@router.delete("/teams/{team_id}", summary="Delete a team entered by hand", operation_id="tournaments_team_delete", response={204: None})
def team_delete(request, team_id: uuid.UUID):
    c.gate_manage(request)
    team = _team(team_id)
    try:
        svc.delete_manual_team(team)
    except svc.TeamDeletionError as error:
        raise Conflict(str(error), "cannot_delete") from error
    return 204, None
