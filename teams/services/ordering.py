import uuid

from django.db import transaction
from django.db.models import F

from ..models import Team


class InvalidTeamOrder(ValueError):
    """The submitted ids repeat a team or name one that isn't the club's."""


def ordered_teams(club) -> list[Team]:
    """The club's teams in their shown order: numbered ones first, then by name."""
    return list(Team.objects.filter(club=club).order_by(F("ordering").asc(nulls_last=True), "name"))


def reorder_teams(club, ids) -> list[Team]:
    """Drag-and-drop on the team list: ``ids`` are the team ids of the page, in the order now
    shown. The page's teams take the slots they occupied in the club's full order, in the new
    sequence, then every team is numbered 1..N so the shown order is the stored one. Returns the
    club's teams in the new order; raises ``InvalidTeamOrder`` for a duplicate, unknown or malformed id."""
    try:
        ids = [str(uuid.UUID(str(value))) for value in ids]
    except (ValueError, TypeError, AttributeError) as error:
        raise InvalidTeamOrder from error
    teams = ordered_teams(club)
    by_id = {str(team.pk): team for team in teams}
    if len(set(ids)) != len(ids) or any(pk not in by_id for pk in ids):
        raise InvalidTeamOrder
    slots = sorted(teams.index(by_id[pk]) for pk in ids)
    for slot, pk in zip(slots, ids, strict=True):
        teams[slot] = by_id[pk]
    for number, team in enumerate(teams, start=1):
        team.ordering = number
    with transaction.atomic():
        Team.objects.bulk_update(teams, ["ordering"])
    return teams
