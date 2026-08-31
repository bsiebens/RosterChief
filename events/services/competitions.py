"""Refreshing a game's score/status from its competition's own data source.

Event.competition (free text) and Event.external_game_id (that source's id for this
fixture) exist so a real integration has somewhere to key off later -- nothing
fetches anything yet. This is that integration's landing spot: the button and view
that call it already exist (management/views.py::EventFetchGameInfoView), so wiring
up a real data source later is a matter of replacing fetch_game_info's body, not
building the UI around it.
"""

import importlib

from django.utils.translation import gettext_lazy as _

from events.models import Competition


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
    competition = Competition.objects.filter(name=event.competition).select_related("flag").first()
    if competition is None or competition.flag is None or not competition.flag.is_active_for_club(event.club):
        return False

    try:
        module = importlib.import_module(competition.module)
        competition_class = getattr(module, competition.name)
    except (ImportError, AttributeError) as error:
        raise CompetitionFetchError(_("“%(name)s” isn't wired up to a real data source yet.") % {"name": competition.name}) from error

    try:
        competition_class().update_game_information(event=event)
    except Exception as error:
        raise CompetitionFetchError(_("Could not fetch game info from “%(name)s”: %(error)s") % {"name": competition.name, "error": error}) from error

    return True
