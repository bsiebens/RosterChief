from ..models import TeamPhoto


def set_team_photo(team, season, image) -> TeamPhoto:
    """The one photo of ``team`` for ``season``: replaces the existing one in place, else creates it."""
    photo = TeamPhoto.objects.filter(team=team, season=season).first() or TeamPhoto(team=team, season=season)
    photo.image = image
    photo.save()
    return photo


def remove_team_photo(team, season_id) -> None:
    TeamPhoto.objects.filter(team=team, season_id=season_id).delete()
