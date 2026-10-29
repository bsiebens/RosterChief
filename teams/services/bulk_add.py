from django.db import transaction

from ..models import StaffAssignment, TeamMembership


def assigned_member_ids(team, season) -> tuple[set, set]:
    """(rostered, staffed) member ids of ``team`` in ``season``."""
    rostered = set(TeamMembership.objects.filter(team=team, season=season).values_list("member_id", flat=True))
    staffed = set(StaffAssignment.objects.filter(team=team, season=season).values_list("member_id", flat=True))
    return rostered, staffed


def add_assignments(team, season, rows) -> tuple[int, int]:
    """Creates one row per validated bulk-add row (``member``, ``position``, optional ``jersey_number`` /
    ``is_captain`` / ``is_alternate_captain``): a staff position becomes a StaffAssignment, anything else a
    TeamMembership. All-or-nothing; a database ``IntegrityError`` (someone else claimed the number or the
    person meanwhile) propagates with nothing saved. Returns (players_added, staff_added)."""
    players_added = staff_added = 0
    with transaction.atomic():
        for row in rows:
            if row["position"].staff_position:
                StaffAssignment.objects.create(team=team, season=season, member=row["member"], position=row["position"])
                staff_added += 1
            else:
                TeamMembership.objects.create(
                    team=team,
                    season=season,
                    member=row["member"],
                    position=row["position"],
                    jersey_number=row.get("jersey_number"),
                    is_captain=row.get("is_captain", False),
                    is_alternate_captain=row.get("is_alternate_captain", False),
                )
                players_added += 1
    return players_added, staff_added
