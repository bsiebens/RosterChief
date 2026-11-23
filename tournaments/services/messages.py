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


def notify_organisers(message: TournamentMessage) -> int:
    """Tell everyone who runs tournaments in this club (the operate tier and above, nobody else) that a team wrote: an inbox entry and a push
    that opens the conversation. A failure here never fails the team's message."""
    import logging

    from django.utils.translation import gettext as _
    from tenant_users.permissions.models import UserTenantPermissions

    from events.services.attendance import current_club
    from members.services import access
    from notifications import inbox

    log = logging.getLogger(__name__)
    registration, tournament = message.registration, message.tournament
    club = current_club()
    title = _("New message from %(team)s") % {"team": registration.display_name if registration else tournament.name}
    body = f"{message.author}: {message.body}"[:200] if message.author else message.body[:200]
    href = f"/tournaments-admin/{tournament.pk}/messages"
    told = 0
    for profile in UserTenantPermissions.objects.select_related("profile"):
        user = profile.profile
        try:
            if user.is_active and access.can_operate_tournaments(user):
                inbox.notify(user, club, title=title, body=body, kind=inbox.Kind.INFO, href=href, push=True, category="general")
                told += 1
        except Exception:
            log.exception("tournaments.message_notify_failed")
    return told
