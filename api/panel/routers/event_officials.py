"""Management referee and official assignment on an event (``/manage/events/{id}/referees|officials``).

The JSON counterpart of the event page's Referees and Officials panels and the assign / add-external
/ remove views (management.views.Event{Referee,Official}{Assign,AddExternal,Remove}View). The panel is
``events.services.crew_panel`` (shared with the page); assigning and removing are
``events.services.referees`` / ``officials`` (capacity is a hard ceiling, an assignment settles the
person's sign-up and tells them).

Who may: reading the panel is the event page's rule (any staff, narrowed by ``scoped_to_managed_teams``;
another club's event is a 404); candidates and sign-ups are only filled for ``can_manage_referees``
(club admin, member admin or a referee-manager grant), which is also the gate of every write
(``403 forbidden``). Officials are a licensed module: with the "officials" feature off the routes are
``404 feature_disabled``.

Not here: the fee line (``fee`` / ``km`` / ``no_fee``) and the payment-form PDFs (the events fees slice).
"""

import uuid
from dataclasses import dataclass

from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.forms import form_error
from api.core.idempotency import idempotent
from api.core.schemas import ErrorOut
from api.panel.events_access import NOT_FOUND, _events
from api.panel.guards import panel_club, require
from api.panel.schemas.event_crew import CrewAssignIn, CrewExternalIn, CrewPanelOut
from api.protected.services import club_features
from club.services.access import can_manage_referees, scoped_to_managed_teams
from events.models import Event, EventOfficial, EventReferee
from events.services import officials as official_service
from events.services import referees as referee_service
from events.services.crew_panel import official_panel, referee_panel
from management.forms import ExternalOfficialForm, ExternalRefereeForm
from members.models import Member

router = Router(tags=["manage-event-crew"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
WRITE_ERRORS = {**ERRORS, 409: ErrorOut, 422: ErrorOut}


@dataclass(frozen=True)
class Crew:
    """What differs between the referee and the official routes."""

    feature: str | None
    panel: callable
    eligible: callable
    assign: callable
    add_external: callable
    remove: callable
    error: type
    assignments: callable  # event -> assignment queryset
    external_form: type
    max_field: str


REFEREES = Crew(
    feature=None,
    panel=referee_panel,
    eligible=lambda event: referee_service.eligible_referees(event, include_expired=True),
    assign=referee_service.assign_referee,
    add_external=referee_service.add_external_referee,
    remove=referee_service.remove_referee,
    error=referee_service.RefereeAssignmentError,
    assignments=lambda event: EventReferee.objects.filter(event=event),
    external_form=ExternalRefereeForm,
    max_field="max_referees",
)
OFFICIALS = Crew(
    feature="officials",
    panel=official_panel,
    eligible=official_service.eligible_officials,
    assign=official_service.assign_official,
    add_external=official_service.add_external_official,
    remove=official_service.remove_official,
    error=official_service.OfficialAssignmentError,
    assignments=lambda event: EventOfficial.objects.filter(event=event),
    external_form=ExternalOfficialForm,
    max_field="max_officials",
)


# --- helpers --------------------------------------------------------------------------------


def _club(request, crew: Crew):
    club = panel_club(request)
    if crew.feature and not club_features(club)[crew.feature]:
        raise ApiError(404, "feature_disabled", _("This feature isn't available for this club."))
    return club


def _club_event(club, event_id) -> Event:
    """Any event of the club, like the web's assign / remove views (not narrowed to the caller's teams)."""
    event = _events(club).filter(pk=event_id).first()
    if event is None:
        raise ApiError(404, "not_found", NOT_FOUND)
    return event


def _name(member) -> str:
    return str(member)


def _panel_out(request, club, crew: Crew, event: Event) -> dict:
    can_manage = can_manage_referees(request.user, club)
    data = crew.panel(event, can_manage=can_manage)
    signups = None
    if can_manage:
        signups = {key: [{"member_id": signup.member_id, "name": _name(signup.member), "status": signup.status, "responded_at": signup.responded_at} for signup in rows] for key, rows in data["signups"].items()}
        signups = {"interested": [], "waiting": [], "declined": [], **signups}
    return {
        "needed": data["needed"],
        "max": getattr(event, crew.max_field),
        "full": data["full"],
        "can_manage": can_manage,
        "assigned": [{"id": row.pk, "member_id": row.member_id, "name": row.display_name, "external": row.member_id is None, "assigned_by": _name(row.assigned_by) if row.assigned_by_id else None} for row in data["assigned"]],
        "candidates": [{"member_id": member.pk, "name": _name(member), "expired": bool(getattr(member, "referee_expired", False)), "has_conflict": member.has_conflict, "conflict_titles": member.conflict_titles} for member in data["candidates"]],
        "signups": signups,
    }


def _assigned_by(request) -> Member | None:
    return Member.objects.filter(user=request.user).first()


def _read(request, crew: Crew, event_id) -> dict:
    club = _club(request, crew)
    event = scoped_to_managed_teams(Event.objects.filter(club=club).prefetch_related("teams"), request.user, club).filter(pk=event_id).first()
    if event is None:
        raise ApiError(404, "not_found", _("Not found."))
    return _panel_out(request, club, crew, event)


def _assign(request, crew: Crew, event_id, payload: CrewAssignIn) -> tuple[int, dict]:
    club = _club(request, crew)
    require(request, club, can_manage_referees)
    event = _club_event(club, event_id)

    def work():
        member = get_object_or_404(crew.eligible(event), pk=payload.member_id)
        try:
            crew.assign(event, member, assigned_by=_assigned_by(request))
        except crew.error as error:
            raise ApiError(409, "cannot_assign", str(error)) from error
        return 201, _panel_out(request, club, crew, _club_event(club, event_id))

    return idempotent(request, work)


def _assign_external(request, crew: Crew, event_id, payload: CrewExternalIn) -> tuple[int, dict]:
    club = _club(request, crew)
    require(request, club, can_manage_referees)
    event = _club_event(club, event_id)
    form = crew.external_form({"name": payload.name})
    if not form.is_valid():
        raise form_error(form)

    def work():
        try:
            crew.add_external(event, form.cleaned_data["name"], assigned_by=_assigned_by(request))
        except crew.error as error:
            raise ApiError(409, "cannot_assign", str(error)) from error
        return 201, _panel_out(request, club, crew, _club_event(club, event_id))

    return idempotent(request, work)


def _unassign(request, crew: Crew, event_id, assignment_id) -> dict:
    club = _club(request, crew)
    require(request, club, can_manage_referees)
    event = _club_event(club, event_id)
    assignment = get_object_or_404(crew.assignments(event).select_related("member"), pk=assignment_id)
    crew.remove(assignment)
    return _panel_out(request, club, crew, _club_event(club, event_id))


# --- referees -------------------------------------------------------------------------------


@router.get("/manage/events/{uuid:event_id}/referees", response={200: CrewPanelOut, **ERRORS}, summary="The event's referee panel")
def referees(request, event_id: uuid.UUID):
    """Who is assigned, the capacity, and for someone who can manage referees the eligible `candidates` (expired
    validity flagged, overlapping events as a warning) and the not-yet-confirmed `signups`. `needed` is false for a
    game the club does not arrange referees for. Any staff who can see the event."""
    return _read(request, REFEREES, event_id)


@router.post("/manage/events/{uuid:event_id}/referees", response={201: CrewPanelOut, **WRITE_ERRORS}, summary="Assign a referee")
def assign_referee(request, event_id: uuid.UUID, payload: CrewAssignIn):
    """Assigns one of the panel's candidates and tells them; their sign-up becomes confirmed. `404` for someone who
    is not a candidate, `409 cannot_assign` when the game is full, not a club-managed home game or the person is
    already assigned. Returns the updated panel. Accepts an `Idempotency-Key`. Club admins, member admins, referee managers."""
    return _assign(request, REFEREES, event_id, payload)


@router.post("/manage/events/{uuid:event_id}/referees/external", response={201: CrewPanelOut, **WRITE_ERRORS}, summary="Log a referee who is not a member")
def add_external_referee(request, event_id: uuid.UUID, payload: CrewExternalIn):
    """Logs a federation-appointed referee by name; counts against the capacity (`409 cannot_assign` when full).
    Accepts an `Idempotency-Key`. Same callers as assigning."""
    return _assign_external(request, REFEREES, event_id, payload)


@router.delete("/manage/events/{uuid:event_id}/referees/{uuid:referee_id}", response={200: CrewPanelOut, **ERRORS}, summary="Unassign a referee")
def remove_referee(request, event_id: uuid.UUID, referee_id: uuid.UUID):
    """Takes the referee off the game; a member is told and their sign-up goes back to `accepted`. Returns the updated panel."""
    return _unassign(request, REFEREES, event_id, referee_id)


# --- officials ------------------------------------------------------------------------------


@router.get("/manage/events/{uuid:event_id}/officials", response={200: CrewPanelOut, **ERRORS}, summary="The event's official panel")
def officials(request, event_id: uuid.UUID):
    """As the referee panel, for match officials. `404 feature_disabled` without the officials module."""
    return _read(request, OFFICIALS, event_id)


@router.post("/manage/events/{uuid:event_id}/officials", response={201: CrewPanelOut, **WRITE_ERRORS}, summary="Assign an official")
def assign_official(request, event_id: uuid.UUID, payload: CrewAssignIn):
    """As assigning a referee, for match officials."""
    return _assign(request, OFFICIALS, event_id, payload)


@router.post("/manage/events/{uuid:event_id}/officials/external", response={201: CrewPanelOut, **WRITE_ERRORS}, summary="Log an official who is not a member")
def add_external_official(request, event_id: uuid.UUID, payload: CrewExternalIn):
    """As logging an external referee, for match officials."""
    return _assign_external(request, OFFICIALS, event_id, payload)


@router.delete("/manage/events/{uuid:event_id}/officials/{uuid:official_id}", response={200: CrewPanelOut, **ERRORS}, summary="Unassign an official")
def remove_official(request, event_id: uuid.UUID, official_id: uuid.UUID):
    """As unassigning a referee, for match officials."""
    return _unassign(request, OFFICIALS, event_id, official_id)
