"""Telling a club member they were put on, or taken off, a game as referee or official (inbox + push, category "crew").

Only members with a login hear anything, never the person who made the change, and nothing is sent for a game that has
already started. Sent after the surrounding transaction commits, so a rolled-back change tells nobody."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from events.services.attendance import current_club
from events.services.event_notifications import describe_when_where

CATEGORY = "crew"


def notify_member(member, event, kind_code: str, *, assigned: bool, actor=None) -> None:
    """Queue the notice for ``member`` (kind_code: ``referee`` or ``official``). A no-op for people without a login, the
    actor themself and games that have started."""
    if member is None or event.start <= timezone.now():
        return
    skip, event_id = getattr(actor, "pk", None), event.pk
    role = _("referee") if kind_code == "referee" else _("official")
    title = _("You were assigned as %(role)s") % {"role": role} if assigned else _("You were taken off a game as %(role)s") % {"role": role}
    body = _("%(game)s, %(when)s.") % {"game": event.title, "when": describe_when_where(event)}

    def send():
        from members.services import recipients
        from notifications import inbox

        club = current_club()
        if club is not None:
            recipients.deliver(member, club, title=title, body=body, skip_user_id=skip, kind=inbox.Kind.INFO if assigned else inbox.Kind.WARNING, href=f"/calendar/events/{event_id}", push=True, category=CATEGORY)

    transaction.on_commit(send)
