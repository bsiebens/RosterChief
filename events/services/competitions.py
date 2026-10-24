"""Refreshing a game's score/status from its competition's own data source.

Event.competition (free text) and Event.external_game_id (that source's id for this
fixture) exist so a real integration has somewhere to key off later -- nothing
fetches anything yet. This is that integration's landing spot: the button and view
that call it already exist (management/views.py::EventFetchGameInfoView), so wiring
up a real data source later is a matter of replacing fetch_game_info's body, not
building the UI around it.
"""

import importlib

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from events.models import Competition
from events.services.game_timeline import refresh_timeline

#: A source's "live" flag can read false for a moment mid-game (a stopped clock, a
#: timeout), so a live game only counts as ended after this many consecutive
#: not-live reads -- one blip must not mark it final or stop the live-score poll.
NOT_LIVE_READS_TO_END = 3
NOT_LIVE_READS_TTL = 60 * 60


class CompetitionFetchError(Exception):
    """A game's info could not be fetched from its competition's data source."""


def fetch_game_info(event) -> bool:
    """Refresh `event`'s score/live status from its competition's data source.

    Gated on that competition's own feature flag being active for the event's
    club: the management form's competition dropdown deliberately shows every
    competition regardless of flag (see management.forms.EventForm), so this is
    where per-club access actually gets enforced. No flag, an inactive one, or
    a competition name that matches nothing -- there's no data source this club
    is allowed to use, so this quietly does nothing and returns False rather
    than erroring: missing access isn't a failure to report.

    Returns whether a fetch was actually attempted.

    Two distinct failure modes, deliberately not folded into one generic
    message any more (see events/competition/hockey.py's own real
    implementations -- this stopped being "nothing's wired up yet" a while
    ago): resolving `competition.module`/`competition.name` to an actual
    class is a *configuration* problem (a typo in either field, or a class
    that was renamed/removed) -- that's the only case that's genuinely "not
    configured". Once that class is found, whatever update_game_information
    itself raises (a network error, an unexpected response shape, a blank
    event.external_game_id, ...) is a *fetch* problem and gets reported with
    its own real message instead, so staff looking at a competition that's
    demonstrably linked correctly aren't told it isn't.
    """
    source = competition_for(event)
    if source is None:
        return False

    was_live = event.is_live
    try:
        source.update_game_information(event=event)
    except Exception as error:
        raise CompetitionFetchError(_("Could not fetch game info from “%(name)s”: %(error)s") % {"name": event.competition, "error": error}) from error

    _debounce_not_live(event, was_live)

    # Same instance, so a source can reuse the payload the score fetch just
    # downloaded. Gated on its own flag; never raises (see game_timeline).
    refresh_timeline(event, source)
    return True


def _debounce_not_live(event, was_live: bool) -> None:
    """Keeps a live game live until the source has said "not live"
    NOT_LIVE_READS_TO_END times in a row (the source's update has already saved
    False by now, so it is put back)."""
    key = f"events:not-live-reads:{event.pk}"
    if event.is_live or not was_live:
        cache.delete(key)
        return
    cache.add(key, 0, NOT_LIVE_READS_TTL)
    if cache.incr(key) < NOT_LIVE_READS_TO_END:
        event.is_live = True
        event.save(update_fields=["is_live"])
    else:
        cache.delete(key)


def competition_for(event):
    """An instance of ``event``'s competition class (events.competition.*), or
    None when there's no data source this club may use (no Competition row, no
    flag, or the flag is off for the club). A Competition whose module/name
    doesn't resolve to a class is a configuration problem and raises
    CompetitionFetchError -- see fetch_game_info's docstring."""
    competition = Competition.objects.filter(name=event.competition).select_related("flag").first()
    if competition is None or competition.flag is None or not competition.flag.is_active_for_club(event.club):
        return None

    try:
        module = importlib.import_module(competition.module)
        competition_class = getattr(module, competition.name)
    except (ImportError, AttributeError) as error:
        raise CompetitionFetchError(_("“%(name)s” isn't wired up to a real data source yet.") % {"name": competition.name}) from error
    return competition_class()
