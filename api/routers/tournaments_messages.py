"""``/tournaments/{id}/messages``: the organisers' side of the conversation with the teams (the teams use their private link, see
``public_tournaments``). Reading and writing need ``tournaments.operate_tournaments`` (the people running the weekend). One thread per
team application plus broadcasts to every team; a message to a team also goes to its contact by email."""

import logging
import uuid

from django.db.models import Count, F, Max, Q
from ninja import Field, Router, Schema

from api.auth import AUTHENTICATORS
from api.routers import tournaments_common as c
from tournaments.models import TournamentMessage, TournamentRegistration
from tournaments.services import notifications
from tournaments.services.messages import message_out

router = Router(auth=AUTHENTICATORS)
log = logging.getLogger(__name__)


class MessageIn(Schema):
    body: str = Field(min_length=1, max_length=4000)


def _author(request) -> str:
    """The sender's name as the club knows them (their member record), never the login email."""
    from members.models import Member

    user = request.user
    member = Member.objects.filter(user=user).first()
    name = member.get_full_name() if member else ""
    return (name or getattr(user, "get_full_name", lambda: "")() or getattr(user, "first_name", "") or "Organiser")[:150]


def _registration(tournament, registration_id) -> TournamentRegistration:
    return c.get_or_404(TournamentRegistration, pk=registration_id, tournament=tournament)


def _mail(registration, body, request) -> None:
    try:
        notifications.send_organiser_message_email(registration, body, request=request)
    except Exception:  # the message is saved either way
        log.exception("tournaments.message_mail_failed")


@router.get("/{tournament_id}/messages", summary="The conversations with the teams", operation_id="tournaments_messages")
def threads(request, tournament_id: uuid.UUID):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    from api.routers.tournaments import _private_registration

    for team in t.teams.filter(registration__isnull=True, team__isnull=True, withdrawn_at__isnull=True):  # teams the organisers added by hand can be written to too
        _private_registration(team)
    regs = TournamentRegistration.objects.filter(tournament=t).exclude(status=TournamentRegistration.Status.REJECTED).annotate(
        unread=Count("messages", filter=Q(messages__sender=TournamentMessage.Sender.TEAM, messages__read_by_organisers=False)), last=Max("messages__created")
    ).order_by(F("last").desc(nulls_last=True), "foreign_name")  # fmt: skip
    rows = []
    for r in regs:
        latest = r.messages.order_by("-created").first()
        rows.append({"registration_id": r.pk, "team": r.foreign_name, "contact": r.foreign_contact_name, "unread": r.unread, "last_message": message_out(latest) if latest else None})
    return {"threads": rows, "unread_total": sum(x["unread"] for x in rows), "broadcasts": [message_out(m) for m in t.messages.filter(registration__isnull=True)]}


@router.get("/{tournament_id}/message-unread", summary="How many team messages are unread", operation_id="tournaments_messages_unread")
def unread(request, tournament_id: uuid.UUID):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    return {"unread": t.messages.filter(sender=TournamentMessage.Sender.TEAM, read_by_organisers=False).count()}


@router.get("/{tournament_id}/messages/{registration_id}", summary="One team's conversation (marks the team's messages read)", operation_id="tournaments_message_thread")
def thread(request, tournament_id: uuid.UUID, registration_id: uuid.UUID):
    c.gate_operate(request)
    r = _registration(c.get_tournament(tournament_id), registration_id)
    r.messages.filter(sender=TournamentMessage.Sender.TEAM, read_by_organisers=False).update(read_by_organisers=True)
    from tournaments.services.messages import thread_for_team

    return {"team": r.foreign_name, "contact": r.foreign_contact_name, "messages": [message_out(m) for m in thread_for_team(r)]}


@router.post("/{tournament_id}/message-broadcast", summary="Write to every team", operation_id="tournaments_message_broadcast", response={201: dict})
def broadcast(request, tournament_id: uuid.UUID, payload: MessageIn):
    c.gate_operate(request)
    t = c.get_tournament(tournament_id)
    message = TournamentMessage.objects.create(tournament=t, sender=TournamentMessage.Sender.ORGANISER, author=_author(request), body=payload.body.strip(), read_by_organisers=True)
    for r in TournamentRegistration.objects.filter(tournament=t).exclude(status=TournamentRegistration.Status.REJECTED):
        _mail(r, message.body, request)
    return 201, message_out(message)


@router.post("/{tournament_id}/messages/{registration_id}", summary="Write to one team", operation_id="tournaments_message_send", response={201: dict})
def send(request, tournament_id: uuid.UUID, registration_id: uuid.UUID, payload: MessageIn):
    c.gate_operate(request)
    r = _registration(c.get_tournament(tournament_id), registration_id)
    message = TournamentMessage.objects.create(tournament=r.tournament, registration=r, sender=TournamentMessage.Sender.ORGANISER, author=_author(request), body=payload.body.strip(), read_by_organisers=True)
    _mail(r, message.body, request)
    return 201, message_out(message)
