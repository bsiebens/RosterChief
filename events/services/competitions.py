"""Which competitions a club may use, and resolving a game's competition to its data source class.

Competitions are ``club.models.Competition`` rows (public schema): ``name`` is what ``Event.competition`` stores,
``module`` the dotted path of the module holding the class named ``name`` (``events.competition.hockey`` + ``RBIHF``),
or the full dotted path of the class itself. Which clubs may use one is decided by its ``feature``: a club needs that
feature (``features.entitlements``). A competition without a feature is offered to nobody (as the old flagless ones).
"""

import importlib

from django.db.models import QuerySet
from django.utils.translation import gettext as _

from events.services.game_timeline import CompetitionFetchError

__all__ = ["CompetitionFetchError", "available_competitions", "competition_allowed", "competition_class", "competition_for", "entitlements_for", "fetch_game_info"]


def entitlements_for(club_or_features=None):
    """An ``Entitlements`` for a club, an ``Entitlements`` itself (anything with ``enabled_codes``), or the current
    schema's club when None. None when there is no club (public schema)."""
    if club_or_features is not None and hasattr(club_or_features, "enabled_codes"):
        return club_or_features
    from features.entitlements import resolve_entitlements

    from .attendance import current_club

    club = club_or_features if club_or_features is not None else current_club()
    if club is None:
        return None
    return resolve_entitlements(club)


def available_competitions(club_or_features=None) -> QuerySet:
    """The Competition rows the club's features allow (the Event competition dropdown, the importers' switch), by
    name. ``club_or_features`` is a Club, an ``Entitlements`` (e.g. ``request.features``) or None for the current club."""
    from club.models import Competition

    features = entitlements_for(club_or_features)
    if features is None:
        return Competition.objects.none()
    return Competition.objects.filter(feature__isnull=False, feature__is_active=True, feature__code__in=features.enabled_codes()).select_related("feature", "sport").order_by("name")


def competition_allowed(name: str, club_or_features=None) -> bool:
    """Whether the club may use the competition called ``name`` (e.g. "RBIHF")."""
    return bool(name) and available_competitions(club_or_features).filter(name=name).exists()


def competition_class(competition):
    """The data source class of a Competition row: ``<module>.<name>``, or ``module`` itself when it names the class.
    A path that resolves to nothing is a configuration problem: CompetitionFetchError."""
    try:
        module = importlib.import_module(competition.module)
        return getattr(module, competition.name)
    except ImportError, AttributeError:
        pass
    module_path, _dot, class_name = competition.module.rpartition(".")
    try:
        return getattr(importlib.import_module(module_path), class_name)
    except (ImportError, AttributeError, ValueError) as error:
        raise CompetitionFetchError(_("“%(name)s” isn't wired up to a real data source yet.") % {"name": competition.name}) from error


def competition_for(event, club_or_features=None):
    """An instance of ``event``'s competition class (events.competition.*), or None when there is no data source this
    club may use (no Competition row by that name, or the club lacks its feature). Raises CompetitionFetchError when the
    row's module/name resolve to no class."""
    if not event.competition:
        return None
    competition = available_competitions(club_or_features).filter(name=event.competition).first()
    if competition is None:
        return None
    return competition_class(competition)()


def fetch_game_info(event, club_or_features=None) -> bool:
    """Refresh ``event``'s score/live status (and timeline) from its competition's data source. Missing access is not a
    failure: returns False without fetching. A fetch problem raises CompetitionFetchError with the source's message.
    Returns whether a fetch was attempted. The debounce and timeline half live in events.services.game_timeline."""
    from events.services import game_timeline

    source = competition_for(event, club_or_features)
    if source is None:
        return False
    game_timeline.fetch_game(event, source)
    return True
