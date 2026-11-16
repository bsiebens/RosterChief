"""Referee management API (``/api/v1/referee-management``): the games that need referees / match officials, who is assigned,
their fees, and the payment forms (HTML preview and PDF).

Gate, in this order: ``events.manage_referees`` (403; the Owner role holds it, so does the platform owner), then the
``referee_officials`` feature (404 ``Not found.``). 404 for unknown ids, 422 ``{detail, code, errors}`` for rule violations.
Writes go through ``events.services.referees`` (audited). ``kind`` is ``referee`` or ``official``."""

import uuid

from django.http import HttpResponse
from django_tenants.utils import get_public_schema_name
from ninja import Query, Router
from ninja.errors import HttpError

from api.schemas_referees import AssignIn, AssignmentOut, AssignmentPatch, CandidateOut, CapacityIn, GameOut, GamesOut, SignupIn, SignupOut, SummaryOut
from club import previews
from events.models import Event
from events.services import payment_forms
from events.services import referees as svc
from members.services import access

router = Router()


def _gate(request):
    access.require_referees(request.user)
    if request.tenant.schema_name == get_public_schema_name() or not request.features.has("referee_officials"):
        raise HttpError(404, "Not found.")


def _game(request, game_id) -> Event:
    _gate(request)
    event = (
        Event.objects.filter(pk=game_id)
        .select_related("location", "opponent")
        .prefetch_related("teams", "referees__member", "referees__referee_type", "officials__member", "officials__referee_type", "referee_signups__member", "official_signups__member")
        .first()
    )
    if event is None:
        raise HttpError(404, "Not found.")
    return event


def _row(request, kind_code, row_id):
    _gate(request)
    kind = svc.kind_of(kind_code)
    row = kind.model.objects.filter(pk=row_id).select_related("event", "member", "referee_type").first()
    if row is None:
        raise HttpError(404, "Not found.")
    return kind, row


@router.get("/summary", response=SummaryOut, summary="Season KPIs, workload per person and the club's referee types (referee permission)")
def get_summary(request):
    _gate(request)
    return svc.summary() | {"types": svc.club_types(request.tenant)}


@router.get("/games", response=GamesOut, summary="Games that need referees or officials, with who is assigned (referee permission)")
def list_games(request, scope: str = "upcoming", range: str = "two_weeks", need: str = "all"):
    """``scope=upcoming`` (default; ``range`` week | two_weeks | 10 | 25 | 50, ``need`` all | open | referees | officials | fees) or
    ``scope=season`` (every game of the current season with people on it, past and upcoming, for the payments).
    ``counts`` has the size of every ``need`` chip."""
    _gate(request)
    return svc.games(scope="season" if scope == "season" else "upcoming", range_=range, need=need)


@router.get("/games/{game_id}", response=GameOut, summary="One game with conflicts and sign-ups worked out (referee permission)")
def get_game(request, game_id: uuid.UUID):
    return svc.game_detail(_game(request, game_id))


@router.get("/games/{game_id}/candidates", response=list[CandidateOut], summary="People who could take this game (referee permission)")
def list_candidates(request, game_id: uuid.UUID, kind: Query[str] = "referee", q: Query[str] = ""):
    """The eligible people. With ``q`` (name words) also other members matching it, flagged ``eligible: false``: anyone can be assigned by hand."""
    event = _game(request, game_id)
    return svc.candidates(event, svc.kind_of(kind), q)


@router.post("/games/{game_id}/assignments", response={201: AssignmentOut}, summary="Assign a member or an external person (referee permission)")
def assign(request, game_id: uuid.UUID, payload: AssignIn):
    event = _game(request, game_id)
    kind = svc.kind_of(payload.kind)
    row = svc.assign(request.user, event, kind, member_id=payload.member_id, external_name=payload.external_name, type_id=payload.referee_type_id, force=payload.force)
    return 201, svc.assignment_out(row)


@router.post("/games/{game_id}/signups", response={201: SignupOut}, summary="Register a sign-up on behalf of an eligible member (referee permission)")
def add_signup(request, game_id: uuid.UUID, payload: SignupIn):
    event = _game(request, game_id)
    kind = svc.kind_of(payload.kind)
    signup = svc.add_signup(request.user, event, kind, payload.member_id)
    return 201, {"id": signup.pk, "kind": kind.code, "member_id": signup.member_id, "name": str(signup.member), "created": signup.created, "can_assign": True, "conflicts": []}


@router.delete("/signups/{kind}/{signup_id}", response={204: None}, summary="Remove a sign-up (referee permission)")
def remove_signup(request, kind: str, signup_id: uuid.UUID):
    _gate(request)
    kind_obj = svc.kind_of(kind)
    signup = kind_obj.signup.objects.filter(pk=signup_id).select_related("member", "event").first()
    if signup is None:
        raise HttpError(404, "Not found.")
    svc.remove_signup(request.user, signup, kind_obj)
    return 204, None


@router.patch("/games/{game_id}/capacity", response=GameOut, summary="How many referees / officials a game takes (referee permission)")
def set_capacity(request, game_id: uuid.UUID, payload: CapacityIn):
    event = _game(request, game_id)
    svc.set_capacity(request.user, event, max_referees=payload.max_referees, max_officials=payload.max_officials)
    return svc.game_detail(event)


@router.patch("/assignments/{kind}/{assignment_id}", response=AssignmentOut, summary="Edit the fee line, type or external name (referee permission)")
def update_assignment(request, kind: str, assignment_id: uuid.UUID, payload: AssignmentPatch):
    kind_obj, row = _row(request, kind, assignment_id)
    row = svc.update(request.user, row, kind_obj, payload.dict(exclude_unset=True))
    return svc.assignment_out(row)


@router.delete("/assignments/{kind}/{assignment_id}", response={204: None}, summary="Take someone off a game (referee permission)")
def remove_assignment(request, kind: str, assignment_id: uuid.UUID):
    kind_obj, row = _row(request, kind, assignment_id)
    svc.remove(request.user, row, kind_obj)
    return 204, None


def _form(request, game_id, kind, lang):
    event = _game(request, game_id)
    svc.kind_of(kind)
    return event, payment_forms.context_for_event(request.tenant, event, kind, lang if lang in payment_forms.LANGUAGES else "en")


@router.get("/games/{game_id}/payment-form", response={200: None}, summary="HTML preview of the payment form of a game (referee permission)")
def payment_form_html(request, game_id: uuid.UUID, kind: str = "referee", lang: str = "en"):
    """``text/html``: the same sheet as the PDF, with the club's saved branding."""
    _event, context = _form(request, game_id, kind, lang)
    return HttpResponse(payment_forms.render_html(context), content_type="text/html; charset=utf-8")


@router.get("/games/{game_id}/payment-form.pdf", response={200: None}, summary="PDF of the payment form of a game (referee permission)")
def payment_form_pdf(request, game_id: uuid.UUID, kind: str = "referee", lang: str = "en"):
    """``application/pdf`` as a download; 503 when PDF rendering is unavailable on this server."""
    event, context = _form(request, game_id, kind, lang)
    try:
        pdf = previews.render_pdf(payment_forms.render_html(context))
    except RuntimeError as e:
        raise HttpError(503, str(e)) from e
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{kind}-payment-form-{event.pk}.pdf"'
    return response
