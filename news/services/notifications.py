"""Telling people about news (inbox + push, category "news"; the editors' review queue is inbox-only).

* ``dispatch_publish_notification``: the audience hears that an item is live. Audience: active members of the current
  season on the item's teams (all active members when club-wide), plus their guardians; one message per account;
  external-only items tell nobody. Each person gets the text in their own language (``Member.language``: English gets the
  English text, everyone else the club's own).
* ``dispatch_notify_editors_of_pending_review``: every holder of ``news.publish_newsitems`` (and the Owner role) hears
  that an item waits for review, except the author.
* ``notify_due`` is the sweep behind the periodic task: published, live and not yet notified.

The ``dispatch_*`` functions queue a Celery task (tenant-aware: it runs in the club schema it was queued from)."""

import logging

from django.utils import timezone

from news.models import News

logger = logging.getLogger(__name__)

CATEGORY = "news"
EXCERPT_WORDS = 15


def _href(item: News) -> str:
    return f"/news/{item.pk}"


def _current_club():
    from events.services.attendance import current_club

    return current_club()


def audience(item: News):
    """Members to tell: active in the current season and, for an item with teams, on one of its rosters that season."""
    from events.models import Season
    from members.models import ClubMembership, Member
    from teams.models import TeamMembership

    season = Season.get_current()
    if season is None:
        return Member.objects.none()
    active = ClubMembership.objects.filter(season=season, status=ClubMembership.StatusChoices.ACTIVE).values("member_id")
    members = Member.objects.filter(pk__in=active)
    team_ids = list(item.teams.values_list("pk", flat=True))
    if team_ids:
        members = members.filter(pk__in=TeamMembership.objects.filter(season=season, team_id__in=team_ids).values("member_id"))
    return members.distinct()


def recipient_users(item: News) -> list:
    """The accounts to tell, once each: the members' own and their guardians' (a parent of three players hears it once)."""
    from members.services import recipients

    users, seen = [], set()
    for member in audience(item).select_related("user"):
        for user, _child in recipients.recipients(member):
            if user.pk not in seen:
                seen.add(user.pk)
                users.append(user)
    return users


def _language_of(users) -> dict:
    from members.models import Member

    return dict(Member.objects.filter(user__in=users).values_list("user_id", "language"))


def send_publish_notification(item: News) -> int:
    """Tell the audience. Returns how many accounts were told. Does not touch ``notified_at``."""
    from news import services as svc
    from notifications import inbox

    club = _current_club()
    if club is None or item.visibility == News.Visibility.EXTERNAL:
        return 0  # external-only news is not in the members' feed: a notification would point at nothing
    users = recipient_users(item)
    languages = _language_of(users)
    texts = {}  # language served -> (title, excerpt)
    for user in users:
        title, body, served = svc.localized_news(item, languages.get(user.pk))
        if served not in texts:
            texts[served] = (title, svc.render_body_excerpt(body, words=EXCERPT_WORDS))
        title, excerpt = texts[served]
        inbox.notify(user, club, title=title, body=excerpt, kind=inbox.Kind.INFO, href=_href(item), push=True, category=CATEGORY)
    return len(users)


def _editors(item: News) -> list:
    from django.contrib.auth import get_user_model

    from authorization.catalog import OWNER_SLUG
    from authorization.models import Role
    from members.services.access import PUBLISH_NEWS

    owner_groups = Role.objects.filter(slug=OWNER_SLUG).values("group_id")
    users = get_user_model().objects.filter(is_active=True).filter(usertenantpermissions__groups__permissions__codename=PUBLISH_NEWS.split(".", 1)[1]) | get_user_model().objects.filter(is_active=True, usertenantpermissions__groups__in=owner_groups)
    author_user_id = item.created_by.user_id if item.created_by_id else None
    return [u for u in users.distinct() if u.pk != author_user_id]


def notify_editors_of_pending_review(item: News) -> int:
    from notifications import inbox

    club = _current_club()
    if club is None:
        return 0
    author = str(item.created_by) if item.created_by_id else "Someone"
    title = f"“{item.title}” is ready for review"
    body = f"{author} submitted this news item for review before it can go live."
    told = 0
    for user in _editors(item):
        inbox.notify(user, club, title=title, body=body, kind=inbox.Kind.INFO, href=_href(item), push=True, category="admin", send_email=False)
        told += 1
    return told


def _claim(news_id, now) -> News | None:
    """Atomically take the right to notify: sets ``notified_at`` only if still empty, so a publish and the sweep never both send."""
    claimed = News.objects.filter(pk=news_id, status=News.Status.PUBLISHED, published_at__lte=now, notified_at__isnull=True).update(notified_at=now)
    return News.objects.select_related("created_by").get(pk=news_id) if claimed else None


def notify_now(news_id, now=None) -> int:
    """Notify the audience of ``news_id`` unless it was notified already, is not live, or is gone; sets ``notified_at``."""
    now = now or timezone.now()
    item = _claim(news_id, now)
    if item is None:
        return 0
    try:
        return send_publish_notification(item)
    except Exception:
        News.objects.filter(pk=news_id).update(notified_at=None)  # let the next sweep retry
        raise


def notify_due(now=None) -> dict:
    """The sweep, in the current club schema: every published, live, not yet notified item."""
    now = now or timezone.now()
    items = told = 0
    for news_id in list(News.objects.filter(status=News.Status.PUBLISHED, published_at__lte=now, notified_at__isnull=True).values_list("pk", flat=True)):
        try:
            told += notify_now(news_id, now)
            items += 1
        except Exception:  # one broken item must not stop the others
            logger.exception("Notifying news item %s failed", news_id)
    return {"items": items, "told": told}


def _enqueue(task_name: str, news_id) -> None:
    from news import tasks

    try:
        getattr(tasks, task_name).delay(str(news_id))
    except Exception:  # a broker hiccup must not fail the request; the publish sweep catches up, a review notice is lost
        logger.exception("news.enqueue_failed %s", task_name)


def dispatch_publish_notification(news_id) -> None:
    """Notify the audience that item ``news_id`` is live (in a background task), then set its ``notified_at``."""
    _enqueue("notify_published", news_id)


def dispatch_notify_editors_of_pending_review(news_id) -> None:
    """Tell the publishers that item ``news_id`` waits for review (in a background task)."""
    _enqueue("notify_editors_of_pending_review", news_id)
