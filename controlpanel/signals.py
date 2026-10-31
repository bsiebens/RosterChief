"""Drop a club's cached dashboard statistics (controlpanel.services.statistics.club_dashboard_stats)
as soon as a row they are computed from changes, so the numbers never lag behind an action the
admin just took. Attendance is deliberately not wired: it changes in bursts, and the cache's
short TTL covers it."""

from django.db.models.signals import post_delete, post_save

from club.models import ClubMembership, ClubRole, OnboardingRequirement, Season
from events.models import Event
from shop.models import Order
from teams.models import StaffAssignment, Team, TeamMembership

from .services.statistics import invalidate_club_stats


def _club_id(instance):
    club_id = getattr(instance, "club_id", None)
    if club_id is None and getattr(instance, "team_id", None) is not None:
        club_id = Team.objects.filter(pk=instance.team_id).values_list("club_id", flat=True).first()
    return club_id


def _invalidate(sender, instance, **kwargs):
    club_id = _club_id(instance)
    if club_id is not None:
        invalidate_club_stats(club_id)


for _model in (ClubMembership, ClubRole, OnboardingRequirement, Season, Event, Order, Team, TeamMembership, StaffAssignment):
    post_save.connect(_invalidate, sender=_model, dispatch_uid=f"club-stats-{_model.__name__}-save")
    post_delete.connect(_invalidate, sender=_model, dispatch_uid=f"club-stats-{_model.__name__}-delete")
