"""Notifying members when a news item goes live.

Scheduled from management.views.NewsPublishView with an ETA matching the
item's own published_at (immediate publishes just get an ETA in the past,
which Celery runs right away -- no separate immediate/scheduled branch
needed). Not on CELERY_BEAT_SCHEDULE -- this fires once, on demand, per
publish, not on a recurring schedule, so it's not in features.jobs.JOB_REGISTRY
either (see features/signals.py: only registered jobs show up on the control
panel's Jobs tab).
"""

from celery import shared_task
from django.utils.html import strip_tags

from club.models import ClubMembership
from club.services.access import current_season
from members.models import Member
from notifications.services import notify_members

from .models import News
from .services import render_body_html


def _notify_audience(news_item):
    """Every current-season, active member linked to this news item -- via its
    teams if it has any, or every active member if it's club-wide. Guardians
    are never part of this set themselves (notifications.services.recipient_emails
    resolves them per member); a guardian ClubMembership has kind=GUARDIAN and
    is excluded the same way teams.services.eligible_roster_members excludes
    it from a roster."""
    season = current_season(news_item.club)
    if season is None:
        return Member.objects.none()

    members = Member.objects.filter(
        member_of__club=news_item.club,
        member_of__season=season,
        member_of__status=ClubMembership.StatusChoices.ACTIVE,
        member_of__kind=ClubMembership.Kind.MEMBER,
    )
    if news_item.teams.exists():
        members = members.filter(team_memberships__team__in=news_item.teams.all(), team_memberships__season=season)
    return members.distinct()


def _plain_text_body(news_item) -> str:
    """The notification's own plain-text body: rendered from the same
    Markdown source the public site would use, then stripped back to plain
    text. Notification.body is reused by every future notification source
    (see that model's own docstring), so it stores plain text, not this one
    source's particular markup."""
    return strip_tags(render_body_html(news_item.body))


@shared_task(name="news.tasks.notify_news_published")
def notify_news_published(news_id):
    news_item = News.objects.filter(pk=news_id, status=News.Status.PUBLISHED).select_related("club").prefetch_related("teams").first()
    if news_item is None:
        # Unpublished or deleted between scheduling this and the ETA firing --
        # nothing to notify about, and not an error worth a JobRun-style alert
        # (this task isn't registered for that anyway -- see the module docstring).
        return "Skipped: not published."

    members = list(_notify_audience(news_item))
    if not members:
        return "Skipped: no current-season active members to notify."

    notifications = notify_members(members, club=news_item.club, title=news_item.title, body=_plain_text_body(news_item), source=news_item)
    return f"Notified {len(notifications)} member(s)."
