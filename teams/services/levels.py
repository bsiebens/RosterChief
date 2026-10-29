"""Referee / official levels and the referee and official lists -- shared by the management screens
(management.views) and the management API (api/panel/routers/levels.py)."""

from django.db.models import Prefetch, ProtectedError

from club.services.access import members_visible_to
from teams.models import OfficialLevel, OfficialProfile, RefereeLevel, RefereeProfile, Team


def delete_level(level) -> bool:
    """Delete a RefereeLevel / OfficialLevel. ``False`` (nothing deleted) when it is still held by someone
    or inherited by another level."""
    try:
        level.delete()
    except ProtectedError:
        return False
    return True


def referee_profiles(user, club):
    """Members of ``club`` the user may see who hold a referee profile, with ``referee_profile`` (the club's one
    profile) and ``eligible_teams`` (``profile.cached_eligible_teams``) already resolved. Ordered by name."""
    members = members_visible_to(user, club, include_guardians=True).filter(referee_profiles__club=club)
    members = members.prefetch_related(Prefetch("referee_profiles", queryset=RefereeProfile.objects.filter(club=club).select_related("level").prefetch_related("excluded_teams"), to_attr="_club_referee_profiles")).order_by("last_name", "first_name")
    return members


def attach_referee_teams(club, members) -> None:
    """Resolve ``member.referee_profile`` and ``.cached_eligible_teams`` for each member of ``referee_profiles`` (one level walk per level)."""
    team_ids_by_level = RefereeLevel.eligible_team_ids_by_level(club)
    teams_by_id = {team.pk: team for team in Team.objects.filter(club=club)}
    for member in members:
        # Exactly one, by unique_referee_profile_per_club_per_member.
        profile = member.referee_profile = member._club_referee_profiles[0]
        team_ids = profile.eligible_team_ids(team_ids_by_level.get(profile.level_id, set()))
        profile.cached_eligible_teams = [teams_by_id[team_id] for team_id in team_ids if team_id in teams_by_id]


def official_profiles(user, club):
    """The officials counterpart to ``referee_profiles``."""
    members = members_visible_to(user, club, include_guardians=True).filter(official_profiles__club=club)
    return members.prefetch_related(Prefetch("official_profiles", queryset=OfficialProfile.objects.filter(club=club).select_related("level"), to_attr="_club_official_profiles")).order_by("last_name", "first_name")


def attach_official_teams(club, members) -> None:
    """The officials counterpart to ``attach_referee_teams``."""
    team_ids_by_level = OfficialLevel.eligible_team_ids_by_level(club)
    teams_by_id = {team.pk: team for team in Team.objects.filter(club=club)}
    for member in members:
        profile = member.official_profile = member._club_official_profiles[0]
        team_ids = team_ids_by_level.get(profile.level_id, set()) if profile.is_eligible else set()
        profile.cached_eligible_teams = [teams_by_id[team_id] for team_id in team_ids if team_id in teams_by_id]
