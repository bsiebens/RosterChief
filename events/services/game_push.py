"""Live game pushes: telling the people who follow a team what just happened in
its game -- goals, penalties, period ends, the result (GAME_TIMELINE.md phase 4).

The moments themselves are detected and stored by events.services.game_moments
(one GameMoment per thing that happened); ``record_moments`` hands each batch
it just stored to ``schedule_delivery``, which pushes it after the transaction
commits, off the polling thread. Decisions behind the rules here (§9):

- **Who:** opt-in, anyone with an account in the club, per team they follow
  (events.GameFollow -- set from the member app or the PWA's "Live game
  updates" screen). Every one of our teams on the game counts.
- **What:** every notifiable moment kind. A moment that isn't ``notifiable``
  (a catch-up after an outage) is never pushed.
- **How much:** the moments one poll detected go out as *one* push per
  account (coalesced: "Goal for us by ..., 4-2" and "Penalty ..." in one
  message). No cap per game -- every poll that found something sends one.
- **Where:** push only -- no Notification/inbox row per goal. Native app
  devices (appapi.push gateway) and web push subscriptions (mobile.services.
  push), each as best-effort as the rest of the app's pushes.
"""

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from events.models import Event, GameFollow, GameMoment
from events.services.game_moments import describe_moment
from rosterchief.concurrency import run_in_background

logger = logging.getLogger(__name__)


def followers(event) -> list:
    """Active accounts following any of our teams on ``event``."""
    user_ids = GameFollow.objects.filter(club=event.club, team__in=event.teams.all()).values_list("user_id", flat=True).distinct()
    return list(get_user_model().objects.filter(pk__in=user_ids, is_active=True))


def compose(event, moments) -> tuple[str, str]:
    """(title, body) for one push: "1st vs Rivals FC" over one line per moment."""
    ours = ", ".join(team.short_name or team.name for team in event.teams.all())
    title = _("%(ours)s vs %(opponent)s") % {"ours": ours, "opponent": event.opponent.name} if event.opponent_id else ours
    body = "\n".join(describe_moment(moment) for moment in moments)
    return title, body


def _event_url(event) -> str:
    try:
        return reverse("mobile:event_detail", kwargs={"pk": event.pk})
    except NoReverseMatch:
        return "/app/"


def _send_native(user, *, title, body, data) -> int:
    from appapi.delivery import push_configured  # deferred: appapi imports back into events
    from appapi.models import PushDevice
    from appapi.push import get_gateway

    if not push_configured():
        return 0
    gateway = get_gateway()
    sent = 0
    for device in PushDevice.objects.filter(user=user, enabled=True).exclude(endpoint_arn=""):
        try:
            alive = gateway.publish(device, title=title, body=body, data=data)
        except Exception:
            logger.exception("game_push.native_failed device=%s", device.pk)
            continue
        if alive:
            sent += 1
        else:
            device.enabled = False
            device.save(update_fields=["enabled", "modified"])
    return sent


def _send_web(club, user, *, title, body, url) -> int:
    from mobile.services.push import send_push_to_user  # deferred: mobile imports back into events

    return send_push_to_user(club, user, title=title, body=body, url=url)


def deliver_moments(event_id, moment_ids) -> int:
    """Pushes the given (just recorded) moments of one game to its followers.
    Every notifiable moment is marked delivered whether or not anyone follows
    the team, so nothing is ever retried later. Returns how many accounts got
    a push."""
    event = Event.objects.select_related("club", "opponent", "location").filter(pk=event_id).first()
    if event is None:
        return 0
    moments = list(GameMoment.objects.filter(event=event, pk__in=moment_ids, notifiable=True, delivered_at__isnull=True).order_by("game_seconds", "created_at"))
    if not moments:
        return 0
    GameMoment.objects.filter(pk__in=[moment.pk for moment in moments]).update(delivered_at=timezone.now())

    title, body = compose(event, moments)
    data = {"club_slug": event.club.slug, "target": {"type": "event", "id": str(event.pk)}, "kind": "game_moment"}
    url = _event_url(event)
    reached = 0
    for user in followers(event):
        if _send_native(user, title=title, body=body, data=data) + _send_web(event.club, user, title=title, body=body, url=url):
            reached += 1
    logger.info("game_push.delivered event_id=%s moments=%s accounts=%s", event.pk, len(moments), reached)
    return reached


def schedule_delivery(event, moment_ids) -> None:
    """After the surrounding transaction commits (the rows must be visible to
    the worker thread), off the polling thread -- like appapi.delivery."""
    if moment_ids:
        ids = list(moment_ids)
        transaction.on_commit(lambda: run_in_background(deliver_moments, event.pk, ids))


def followed_team_ids(user, club) -> set:
    return set(GameFollow.objects.filter(club=club, user=user).values_list("team_id", flat=True))


def set_followed_teams(user, club, team_ids) -> set:
    """Replaces ``user``'s followed teams in ``club`` with ``team_ids`` (only
    this club's own teams are kept). Returns the resulting set."""
    from teams.models import Team

    wanted = set(Team.objects.filter(club=club, pk__in=list(team_ids)).values_list("pk", flat=True))
    with transaction.atomic():
        GameFollow.objects.filter(club=club, user=user).exclude(team_id__in=wanted).delete()
        existing = followed_team_ids(user, club)
        GameFollow.objects.bulk_create([GameFollow(club=club, user=user, team_id=team_id) for team_id in wanted - existing])
    return wanted
