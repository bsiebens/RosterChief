"""Push alerts for the public tournament pages: visitors follow teams (``TournamentFollower``, no account) and get a push when a game of
a followed team is about to start, starts, has a goal or a penalty, ends, or when a block (not lunch) is about to start.

Event alerts (start, goal, penalty, end) are sent when the staff record them (queued after the request's transaction commits); the timed
ones (5 minutes before a game or block) come from a job that runs every minute, ``send_due_alerts``. A browser whose push service says the
subscription is gone is forgotten."""

import datetime
import logging

from django.db import transaction
from django.utils import timezone

from ..models import Match, ScheduleBlock, Tournament, TournamentAlertLog, TournamentFollower

log = logging.getLogger(__name__)

LEAD = datetime.timedelta(minutes=5)

#: (title, body) per kind and language. ``{home}``/``{away}`` are team names, ``{score}`` the running score, ``{who}`` a player.
TEXT = {
    "pre_game": {
        "en": ("Game in 5 minutes", "{home} – {away}{where}"),
        "nl": ("Wedstrijd over 5 minuten", "{home} – {away}{where}"),
        "fr": ("Match dans 5 minutes", "{home} – {away}{where}"),
    },
    "start": {"en": ("Game started", "{home} – {away}"), "nl": ("Wedstrijd gestart", "{home} – {away}"), "fr": ("Match commencé", "{home} – {away}")},
    "goal": {"en": ("Goal! {score}", "{team} scored · {home} – {away}"), "nl": ("Doelpunt! {score}", "{team} scoort · {home} – {away}"), "fr": ("But ! {score}", "{team} marque · {home} – {away}")},
    "penalty": {"en": ("Penalty", "{team}: {who} · {home} – {away}"), "nl": ("Strafbank", "{team}: {who} · {home} – {away}"), "fr": ("Pénalité", "{team} : {who} · {home} – {away}")},
    "end": {"en": ("Game over: {score}", "{home} – {away}"), "nl": ("Wedstrijd afgelopen: {score}", "{home} – {away}"), "fr": ("Match terminé : {score}", "{home} – {away}")},
    "block": {"en": ("{title} in 5 minutes", "{when}{where}"), "nl": ("{title} over 5 minuten", "{when}{where}"), "fr": ("{title} dans 5 minutes", "{when}{where}")},
}
PREF_OF = {"pre_game": "pre_game", "start": "start", "goal": "goals", "penalty": "penalties", "end": "end", "block": "blocks"}


def _text(kind: str, lang: str, **ctx) -> tuple[str, str]:
    title, body = TEXT[kind].get(lang) or TEXT[kind]["en"]
    return title.format(**ctx), body.format(**ctx)


def _push(follower: TournamentFollower, title: str, body: str, href: str) -> None:
    from notifications import gateways

    message = gateways.Message(title=title, body=body, data={"href": href, "url": href})
    result = gateways.WebPushGateway().send(gateways.Target(channel="web_push", provider="webpush", key=follower.endpoint, obj=follower), message)
    if result.outcome == gateways.DEAD:
        follower.delete()
    elif result.outcome not in (gateways.SENT,):
        log.info("tournaments.alert_not_sent", extra={"outcome": result.outcome, "error": result.error})


def notify(tournament: Tournament, team_ids, kind: str, href: str, **ctx) -> int:
    """Send ``kind`` to everyone following one of ``team_ids`` (or, with no teams given, everyone following anything) who wants it."""
    followers = tournament.followers.all()
    if team_ids:
        followers = followers.filter(teams__in=list(team_ids)).distinct()
    else:
        followers = followers.filter(teams__isnull=False).distinct()
    sent = 0
    for follower in followers:
        if not follower.wants(PREF_OF[kind]):
            continue
        title, body = _text(kind, follower.lang, **ctx)
        _push(follower, title, body, href)
        sent += 1
    return sent


def _match_ctx(match: Match) -> dict:
    return {"home": match.home_team.display_name if match.home_team else match.home_display, "away": match.away_team.display_name if match.away_team else match.away_display}


def _game_href(match: Match) -> str:
    return f"{match.stage.tournament.public_path}/games/{match.pk}"


def _team_ids(match: Match) -> list:
    return [t for t in (match.home_team_id, match.away_team_id) if t]


def _score(match: Match) -> str:
    """The running or final score of the whole game (both periods of a back-and-forth tie added up)."""
    first = match.first_leg
    parts = [m for m in (first, match) if m is not None]
    return f"{sum(m.score_home or 0 for m in parts)}–{sum(m.score_away or 0 for m in parts)}"


def game_started(match_id) -> None:
    match = Match.objects.select_related("stage__tournament", "home_team__team", "away_team__team").get(pk=match_id)
    notify(match.stage.tournament, _team_ids(match), "start", _game_href(match), **_match_ctx(match))


def game_ended(match_id) -> None:
    match = Match.objects.select_related("stage__tournament", "home_team__team", "away_team__team").get(pk=match_id)
    notify(match.stage.tournament, _team_ids(match), "end", _game_href(match), score=_score(match), **_match_ctx(match))


def event_recorded(event_id) -> None:
    from ..models import MatchEvent

    event = MatchEvent.objects.select_related("match__stage__tournament", "match__home_team__team", "match__away_team__team").filter(pk=event_id).first()
    if event is None or event.type not in (MatchEvent.Type.GOAL, MatchEvent.Type.PENALTY) or not event.side:
        return
    match = event.match
    ctx = _match_ctx(match)
    team = ctx["home"] if event.side == "home" else ctx["away"]
    who = f"#{(event.details or {}).get('number')} {event.player}".replace("#None ", "").strip() if event.player else ""
    kind = "goal" if event.type == MatchEvent.Type.GOAL else "penalty"
    notify(match.stage.tournament, _team_ids(match), kind, _game_href(match), score=_score(match), team=team, who=who or team, **ctx)


def after_commit(function, *args) -> None:
    """Queue an alert for after the request's transaction: never while the staff's save can still roll back, never failing the save."""

    def run():
        try:
            from .. import tasks

            tasks.send_alert.delay(function.__name__, *[str(a) for a in args])
        except Exception:  # an alert is a nicety: the score is saved either way
            log.exception("tournaments.alert_queue_failed")

    transaction.on_commit(run)


def send_due_alerts(now=None) -> int:
    """Games and blocks that start within 5 minutes and have not been alerted yet (run every minute, in one club's schema)."""
    now = now or timezone.now()
    sent = 0
    live = Tournament.objects.filter(status__in=[Tournament.Status.PUBLISHED, Tournament.Status.IN_PROGRESS], followers__isnull=False).distinct()
    for tournament in live:
        matches = (
            Match.objects.filter(stage__tournament=tournament, status=Match.Status.SCHEDULED, scheduled_start__gt=now, scheduled_start__lte=now + LEAD)
            .exclude(home_team__isnull=True).exclude(away_team__isnull=True)
            .select_related("stage__tournament", "home_team__team", "away_team__team", "field")
        )  # fmt: skip
        for match in matches:
            _, fresh = TournamentAlertLog.objects.get_or_create(tournament=tournament, kind="pre_game", key=str(match.pk))
            if fresh:
                where = f" · {match.field.name}" if match.field_id else ""
                sent += notify(tournament, _team_ids(match), "pre_game", _game_href(match), where=where, **_match_ctx(match))
        blocks = ScheduleBlock.objects.filter(tournament=tournament, kind=ScheduleBlock.Kind.BLOCK, starts_at__gt=now, starts_at__lte=now + LEAD).prefetch_related("teams").select_related("field")
        for block in blocks:
            _, fresh = TournamentAlertLog.objects.get_or_create(tournament=tournament, kind="block", key=str(block.pk))
            if fresh:
                where = f" · {block.location or (block.field.name if block.field_id else '')}".rstrip(" ·")
                when = timezone.localtime(block.starts_at).strftime("%H:%M")
                sent += notify(tournament, [t.pk for t in block.teams.all()], "block", tournament.public_path, title=block.title, when=when, where=where)
    return sent
