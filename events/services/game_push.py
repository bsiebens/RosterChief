"""Live game pushes: telling the people who follow a game what just happened in it -- goals, penalties, period ends,
the result (GAME_TIMELINE.md phase 4 in the old app).

The moments themselves are detected and stored by events.services.game_moments (one GameMoment per thing that
happened); ``record_moments`` hands each batch it just stored to ``schedule_delivery``, which delivers it once the
surrounding transaction commits. The rules (unchanged from the old app):

- **Who:** opt-in, any account following one of the game's teams (events.GameFollow) or the game itself
  (events.EventFollow) -- see events.services.follows. Coaches and players are not pushed unless they follow.
- **What:** every notifiable moment kind. A moment that isn't ``notifiable`` (a catch-up after an outage) is never
  pushed.
- **How much:** the moments one poll detected go out as *one* message per account (title = the scoreboard, one body
  line per moment, events.services.game_texts). No cap per game.
- **Where:** the club inbox with push (notifications.inbox.notify, category "game"): the push engine applies each
  user's own settings (a user who switched game pushes off still gets the inbox entry). The inbox write and its push
  outbox entry are one transaction, so delivery is at-least-once.
"""

import logging

from django.db import connection, transaction
from django.utils import timezone
from django_tenants.utils import schema_context

from events.models import Event, GameMoment
from events.services.follows import followers
from events.services.game_texts import compose
from notifications import inbox
from notifications.models import Category

logger = logging.getLogger(__name__)


def event_href(event) -> str:
    """Where the push leads in the management app."""
    return f"/calendar/events/{event.pk}"


def _current_club():
    from events.services.attendance import current_club  # deferred: attendance is a sibling service with heavier imports

    return current_club()


def deliver_moments(event_id, moment_ids, *, club=None) -> int:
    """Delivers the given (just recorded) moments of one game to its followers, in game-clock order. Every notifiable
    moment of the batch is marked delivered afterwards, whether or not anyone follows the game, so nothing is retried
    later and a moment is never sent twice. Runs in the club's schema; ``club`` defaults to the active one. Returns how
    many accounts were notified."""
    event = Event.objects.select_related("opponent", "location").filter(pk=event_id).first()
    if event is None:
        return 0
    moments = list(GameMoment.objects.filter(event=event, pk__in=list(moment_ids), notifiable=True, delivered_at__isnull=True).order_by("game_seconds", "created"))
    if not moments:
        return 0
    club = club or _current_club()
    title, body = compose(event, moments)
    reached = 0
    with transaction.atomic():
        if club is not None:
            for user in followers(event):
                inbox.notify(user, club, title=title, body=body, kind=inbox.Kind.INFO, href=event_href(event), push=True, category=Category.GAME)
                reached += 1
        else:  # pragma: no cover -- only outside a club schema, which never holds games
            logger.warning("game_push.no_club event_id=%s", event.pk)
        GameMoment.objects.filter(pk__in=[moment.pk for moment in moments]).update(delivered_at=timezone.now())
    logger.info("game_push.delivered event_id=%s moments=%s accounts=%s", event.pk, len(moments), reached)
    return reached


def schedule_delivery(event, moment_ids) -> None:
    """Delivers ``moment_ids`` after the surrounding transaction commits (right away in autocommit), in the schema that
    is active now -- the caller may have left its ``schema_context`` by the time the commit happens."""
    if not moment_ids:
        return
    schema = connection.schema_name
    ids = [str(pk) for pk in moment_ids]

    def run():
        try:
            with schema_context(schema):
                deliver_moments(event.pk, ids)
        except Exception:  # a push problem must never break the timeline update; the moments stay undelivered
            logger.exception("game_push.delivery_failed event_id=%s", event.pk)

    transaction.on_commit(run)
