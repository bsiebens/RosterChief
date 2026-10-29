"""Live Activities (iOS lock screen / Dynamic Island) for the games an account follows.

Driven by the same ``game_timeline_changed`` signal as the pushes in game_push:

* while a game is live, every follower's device that sent a push-to-start token and has no
  activity for the game yet gets a ``start`` push; the device then registers the new
  activity's own token (``POST /events/{id}/live-activity``);
* every change of the timeline sends an ``update`` to the registered activities;
* when the game turns final the activities get an ``end`` (kept on the lock screen for an
  hour) and are forgotten.

A start with no activity token yet is remembered as a ``LiveActivity`` row with a blank
token, so the next change doesn't start a second activity on that device. Dead tokens are
dropped: a push-to-start token is cleared, an activity row deleted.
"""

import logging
import time

from django.db import transaction
from django.utils import translation
from django.utils.translation import gettext as _

from events.models import Event
from events.services.game_push import followers, language_for_locale
from rosterchief.concurrency import run_in_background

logger = logging.getLogger(__name__)

#: How long a finished game's activity stays on the lock screen.
DISMISS_AFTER_SECONDS = 60 * 60
#: When the device should grey the activity out if no update arrived (the clock is shown
#: interpolated, but the feed is polled about once a minute).
STALE_AFTER_SECONDS = 15 * 60


def _score_of(event, document: dict) -> tuple[int | None, int | None]:
    score = document.get("score") or {}
    if score:
        return score.get("home"), score.get("away")
    if event.score_for is not None and event.score_against is not None:
        return (event.score_for, event.score_against) if event.is_home_game else (event.score_against, event.score_for)
    return None, None


def content_state(event, document: dict) -> dict:
    """What the activity shows: both sides, the score, the period and clock the federation
    reports, and ``status`` (``live`` or ``final``). Keys are snake_case; the app maps them in
    its ``ContentState`` Codable."""
    ours = ", ".join(team.short_name or team.name for team in event.teams.all()) or "?"
    theirs = event.opponent.name if event.opponent_id else "?"
    home_score, away_score = _score_of(event, document)
    return {
        "home": ours if event.is_home_game else theirs,
        "away": theirs if event.is_home_game else ours,
        "home_score": home_score,
        "away_score": away_score,
        "period": document.get("period") or "",
        "clock": document.get("clock") or "",
        "status": "final" if document.get("status") == "final" else "live",
    }


def attributes(event) -> dict:
    """The activity's fixed data, set once at start."""
    return {"event_id": str(event.pk), "club_slug": event.club.slug}


def _alert(event, state, language) -> dict:
    with translation.override(language):
        return {"title": f"{state['home']} - {state['away']}", "body": _("The game started.")}


def on_timeline_changed(sender, *, event, previous, current, first_fetch, **kwargs) -> None:
    """game_timeline_changed receiver: after the surrounding transaction commits, off the polling
    thread."""
    status, previous_status = current.get("status"), (previous or {}).get("status")
    if previous_status == "final" or status not in ("live", "final"):
        return  # a post-game correction or a game that has not started: nothing on a lock screen
    transaction.on_commit(lambda: run_in_background(deliver, event.pk, "end" if status == "final" else "live"))


def deliver(event_id, phase: str) -> int:
    """``phase`` "live": start where wanted, update the rest. "end": end every activity.
    Returns how many pushes were handed to the gateway."""
    from api.models import LiveActivity, PushDevice
    from api.protected.delivery import push_configured  # deferred: api.protected imports events
    from api.protected.push import get_gateway

    if not push_configured():
        return 0
    event = Event.objects.select_related("club", "opponent", "timeline").prefetch_related("teams").filter(pk=event_id).first()
    timeline = getattr(event, "timeline", None) if event else None
    if event is None or timeline is None:
        return 0
    state = content_state(event, timeline.data or {})
    gateway = get_gateway()
    now = int(time.time())
    sent = 0

    activities = list(LiveActivity.objects.filter(event=event).select_related("device"))
    if phase == "end":
        for activity in activities:
            if activity.token and _send(gateway, activity.device, activity.token, event="end", state=state, dismiss_at=now + DISMISS_AFTER_SECONDS):
                sent += 1
        LiveActivity.objects.filter(event=event).delete()
        return sent

    started_devices = {activity.device_id for activity in activities}
    for user in followers(event):
        for device in PushDevice.objects.filter(user=user, enabled=True, platform=PushDevice.Platform.IOS).exclude(push_to_start_token=""):
            if device.pk in started_devices:
                continue
            alert = _alert(event, state, language_for_locale(device.locale))
            if _send(gateway, device, device.push_to_start_token, event="start", state=state, attributes=attributes(event), alert=alert, stale_at=now + STALE_AFTER_SECONDS, on_dead="clear_start_token"):
                LiveActivity.objects.get_or_create(device=device, event=event, defaults={"club": event.club, "token": ""})
                sent += 1
    for activity in activities:
        if activity.token and _send(gateway, activity.device, activity.token, event="update", state=state, stale_at=now + STALE_AFTER_SECONDS, on_dead=activity):
            sent += 1
    logger.info("live_activity.delivered event_id=%s phase=%s pushes=%s", event.pk, phase, sent)
    return sent


def _send(gateway, device, token, *, event, state, attributes=None, alert=None, stale_at=None, dismiss_at=None, on_dead=None) -> bool:
    """One push; True when handed to Apple. A dead token is cleaned up per ``on_dead``: the
    string "clear_start_token" clears the device's push-to-start token, a LiveActivity row is
    deleted."""
    try:
        alive = gateway.live_activity(device, token=token, event=event, content_state=state, attributes=attributes, alert=alert, stale_at=stale_at, dismiss_at=dismiss_at)
    except Exception:
        logger.exception("live_activity.failed device=%s event=%s", device.pk, event)
        return False
    if alive:
        return True
    if on_dead == "clear_start_token":
        device.push_to_start_token = ""
        device.save(update_fields=["push_to_start_token", "modified"])
    elif on_dead is not None:
        on_dead.delete()
    return False
