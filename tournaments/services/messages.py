"""The conversation between the organisers of a tournament and its teams. A message belongs to one team's application (the team reaches
it through its private link) or, without a registration, is a broadcast every team sees."""

from django.db.models import Q

from ..models import TournamentMessage, TournamentRegistration


def thread_for_team(registration: TournamentRegistration):
    return TournamentMessage.objects.filter(Q(registration=registration) | Q(registration__isnull=True, tournament_id=registration.tournament_id)).order_by("created")


def unread_for_team(registration: TournamentRegistration) -> int:
    rows = thread_for_team(registration).filter(sender=TournamentMessage.Sender.ORGANISER)
    if registration.messages_seen_at:
        rows = rows.filter(created__gt=registration.messages_seen_at)
    return rows.count()


def message_out(m: TournamentMessage) -> dict:
    return {"id": m.pk, "sender": m.sender, "author": m.author, "body": m.body, "created": m.created, "broadcast": m.registration_id is None}
