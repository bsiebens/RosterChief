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
  account (coalesced: "GOAL Sharks U16: ..." and "Penalty ..." in one
  message). No cap per game -- every poll that found something sends one.
- **Where:** every channel through notifications.dispatch (native APNs/FCM
  devices, web push) -- no Notification/inbox row per goal. Each channel is
  as best-effort as the rest of the app's pushes.
"""

import datetime
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.urls import NoReverseMatch, reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from events.models import Event, EventFollow, GameFollow, GameMoment
from events.services.game_moments import PENALTY_KIND_LABELS, person_label
from notifications.dispatch import dispatch_to_user
from notifications.models import NotificationCategory

logger = logging.getLogger(__name__)


def followers(event) -> list:
    """Active accounts following ``event``: any of our teams on it, or the game itself."""
    team_followers = GameFollow.objects.filter(club=event.club, team__in=event.teams.all()).values_list("user_id", flat=True)
    game_followers = EventFollow.objects.filter(club=event.club, event=event).values_list("user_id", flat=True)
    user_ids = set(team_followers) | set(game_followers)
    return list(get_user_model().objects.filter(pk__in=user_ids, is_active=True))


def _team_names(event) -> tuple[str, str]:
    """(ours, theirs) as people know them: our team(s)' short names, the opponent's name."""
    ours = ", ".join(team.short_name or team.name for team in event.teams.all())
    return ours, event.opponent.name if event.opponent_id else _("Opponent")


def score_line(event, score) -> str:
    """ "Sharks U16 4-2 Bulldogs Liège", the home team first: at an away location the opponent leads
    (and the score flips with it). No location, or a home one, reads ours first."""
    ours, theirs = _team_names(event)
    ours_goals, theirs_goals = score.get("for", 0), score.get("against", 0)
    if event.location_id is not None and not event.location.is_home:
        return f"{theirs} {theirs_goals}\u2013{ours_goals} {ours}"
    return f"{ours} {ours_goals}\u2013{theirs_goals} {theirs}"


def push_line(event, moment) -> str:
    """One translated line for a push: what happened, to which team, and who."""
    ours, theirs = _team_names(event)
    payload = moment.payload or {}
    kinds = GameMoment.Kind
    ours_side = moment.kind in (kinds.GOAL_FOR, kinds.PENALTY_FOR, kinds.PENALTY_SHOT_FOR)
    team = ours if ours_side else theirs
    if moment.kind == kinds.GAME_STARTED:
        return _("\U0001f3d2 The game has started")
    if moment.kind in (kinds.GOAL_FOR, kinds.GOAL_AGAINST):
        values = {"team": team, "player": person_label(payload.get("scorer")) or "?"}
        # Only our own goals name the assists; an opponent's is just the scorer.
        assists = [name for name in (person_label(person) for person in payload.get("assists") or []) if name] if ours_side else []
        if assists:
            values["assists"] = ", ".join(assists)
            return ngettext("\U0001f945 GOAL %(team)s: %(player)s \u00b7 assist %(assists)s", "\U0001f945 GOAL %(team)s: %(player)s \u00b7 assists %(assists)s", len(assists)) % values
        return _("\U0001f945 GOAL %(team)s: %(player)s") % values
    if moment.kind == kinds.GOAL_CANCELLED:
        return _("\u21a9\ufe0f Goal taken back")
    if moment.kind in (kinds.PENALTY_FOR, kinds.PENALTY_AGAINST):
        reason = payload.get("description") or ""
        if payload.get("kind") not in (None, "", "minor"):
            reason = _("%(reason)s (%(kind)s)") % {"reason": reason, "kind": str(PENALTY_KIND_LABELS.get(payload["kind"], payload["kind"])).lower()}
        line = _("\U0001f6a8 Penalty %(team)s: %(player)s") % {"team": team, "player": person_label(payload.get("player")) or "?"}
        details = [reason] if reason else []
        if payload.get("minutes"):
            details.append(_("%(minutes)s min") % {"minutes": payload["minutes"]})
        return " \u00b7 ".join([line, *details])
    if moment.kind in (kinds.PENALTY_SHOT_FOR, kinds.PENALTY_SHOT_AGAINST):
        return _("\U0001f3af Penalty shot for %(team)s") % {"team": team}
    if moment.kind == kinds.PERIOD_ENDED:
        return _("\u23f8 End of period %(period)s") % {"period": payload.get("period") or ""}
    if moment.kind == kinds.GAME_FINAL:
        return _("\U0001f3c1 Full time")
    return moment.get_kind_display()


def compose(event, moments) -> tuple[str, str]:
    """(title, body) for one push. The title is the score ("Sharks U16 4\u20132 Bulldogs Li\u00e8ge", home team
    first) as of the latest moment in the batch that carries one -- or the result ("Sharks U16 win 4\u20132") for the final, or
    "Sharks U16 vs Bulldogs Li\u00e8ge" while there's no score yet. The body is one line per moment."""
    ours, theirs = _team_names(event)
    last = next((moment for moment in reversed(moments) if (moment.payload or {}).get("score")), moments[-1])
    score = (last.payload or {}).get("score")
    body_lines = [push_line(event, moment) for moment in moments]
    if not score:
        return _("%(ours)s vs %(opponent)s") % {"ours": ours, "opponent": theirs}, "\n".join(body_lines)
    if last.kind == GameMoment.Kind.GAME_FINAL:
        goals = f"{score.get('for', 0)}\u2013{score.get('against', 0)}"
        if score.get("for", 0) > score.get("against", 0):
            title = _("%(team)s win %(score)s") % {"team": ours, "score": goals}
        elif score.get("for", 0) < score.get("against", 0):
            title = _("%(team)s lose %(score)s") % {"team": ours, "score": goals}
        else:
            title = _("Draw %(score)s") % {"score": goals}
        return title, "\n".join([_("\U0001f3c1 Final \u00b7 %(line)s") % {"line": score_line(event, score)}, *body_lines[:-1]])
    return score_line(event, score), "\n".join(body_lines)


def _event_url(event) -> str:
    try:
        return reverse("mobile:event_detail", kwargs={"pk": event.pk})
    except NoReverseMatch:
        return "/app/"


def language_for_locale(locale) -> str:
    """The language in settings.LANGUAGES a device locale ("nl-NL", "nl_BE", "en") maps to, by its
    first two letters; English when it is empty or not one we translate into."""
    code = (locale or "").replace("_", "-").split("-")[0].lower()
    return code if code in {language for language, _name in settings.LANGUAGES} else "en"


def deliver_moments(event_id, moment_ids) -> int:
    """Pushes the given (just recorded) moments of one game to its followers.
    Every notifiable moment is marked delivered -- after the dispatch -- whether
    or not anyone follows the team. At-least-once: a crash mid-dispatch leaves
    the batch unmarked, so the outbox retry sends it again. Returns how many accounts got
    a push."""
    event = Event.objects.select_related("club", "opponent", "location").filter(pk=event_id).first()
    if event is None:
        return 0
    moments = list(GameMoment.objects.filter(event=event, pk__in=moment_ids, notifiable=True, delivered_at__isnull=True).order_by("game_seconds", "created_at"))
    if not moments:
        return 0

    composed: dict = {}

    def texts(language):
        """``compose`` once per distinct language, under that language's override."""
        if language not in composed:
            with translation.override(language):
                composed[language] = compose(event, moments)
        return composed[language]

    data = {"club_slug": event.club.slug, "target": {"type": "event", "id": str(event.pk)}, "kind": "game_moment", "category": "game"}
    url = _event_url(event)
    reached = 0
    for user in followers(event):
        if dispatch_to_user(club=event.club, user=user, texts=texts, data=data, url=url, thread_id=str(event.pk), category=NotificationCategory.GAME).total:
            reached += 1
    # Marked only now that the pushes went out: a crash before this repeats at most this batch
    # (at-least-once) instead of dropping it for good.
    GameMoment.objects.filter(pk__in=[moment.pk for moment in moments]).update(delivered_at=timezone.now())
    logger.info("game_push.delivered event_id=%s moments=%s accounts=%s", event.pk, len(moments), reached)
    return reached


def schedule_delivery(event, moment_ids) -> None:
    """Writes an outbox entry in the surrounding transaction (the moments and the work to push them
    commit together) and works it off after commit, off the polling thread."""
    if moment_ids:
        from notifications import outbox
        from notifications.models import OutboxEntry

        entry = outbox.enqueue(OutboxEntry.Kind.GAME_MOMENTS, {"event_id": str(event.pk), "moment_ids": [str(pk) for pk in moment_ids]})
        outbox.kick([entry.pk])


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


# -- following one game, and what Home shows for the games an account follows -------------------

#: A followed game is on Home from this long before it starts ...
FOLLOWED_GAME_LOOKAHEAD = datetime.timedelta(hours=24)
#: ... through the time it is live, until this long after its start (a game takes ~2 hours, so the
#: result stays up for a few hours afterwards).
FOLLOWED_GAME_LOOKBACK = datetime.timedelta(hours=6)
#: At most this many followed-game cards on Home (the Calendar has everything).
FOLLOWED_GAMES_LIMIT = 5


def followed_event_ids(user, club) -> set:
    """The games ``user`` follows directly (not counting the ones their followed teams play)."""
    return set(EventFollow.objects.filter(club=club, user=user).values_list("event_id", flat=True))


def follow_event(user, club, event) -> None:
    """Idempotent."""
    EventFollow.objects.get_or_create(club=club, user=user, event=event)


def unfollow_event(user, club, event) -> None:
    """Idempotent."""
    EventFollow.objects.filter(club=club, user=user, event=event).delete()


def follow_state(user, club, event) -> dict:
    """``{"directly": bool, "via_team": bool}`` for ``user`` and ``event``."""
    directly = EventFollow.objects.filter(club=club, user=user, event=event).exists()
    via_team = GameFollow.objects.filter(club=club, user=user, team__in=event.teams.all()).exists()
    return {"directly": directly, "via_team": via_team}


def followed_games(user, club, *, now=None, limit=FOLLOWED_GAMES_LIMIT) -> list[dict]:
    """Rows ``{"event", "via"}`` for the games ``user`` follows -- directly or through a followed
    team -- that are worth a card on Home right now: live, starting within the next day, or begun
    within the last few hours. ``via`` is ``["game"]``, ``["team"]`` or both. Live games first, then
    what is coming up soonest, then what just finished (latest first); at most ``limit``."""
    now = now or timezone.now()
    direct_ids = followed_event_ids(user, club)
    team_ids = followed_team_ids(user, club)
    if not direct_ids and not team_ids:
        return []

    in_window = Q(is_live=True) | Q(start__gte=now - FOLLOWED_GAME_LOOKBACK, start__lte=now + FOLLOWED_GAME_LOOKAHEAD)
    followed = Q(pk__in=direct_ids) | Q(teams__in=team_ids)
    events = list(Event.objects.filter(club=club, kind=Event.EventKind.GAME, cancelled=False).filter(followed).filter(in_window).distinct().select_related("location", "opponent", "timeline").prefetch_related("teams"))

    def order(event):
        if event.is_live:
            return (0, event.start)
        if event.start > now:
            return (1, event.start)
        return (2, -event.start.timestamp())

    rows = []
    for event in sorted(events, key=order)[:limit]:
        via = []
        if event.pk in direct_ids:
            via.append("game")
        if any(team.pk in team_ids for team in event.teams.all()):
            via.append("team")
        rows.append({"event": event, "via": via})
    return rows
