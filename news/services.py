"""Markdown rendering for News.body.

Club staff author `body` as Markdown (see NewsForm's help text) -- rendered
to HTML by render_body_html below everywhere it's actually shown: the
public API (news/api.py), mobile's own article page, and the control
panel's own preview (management/templates/management/_news_preview.html),
so staff see the same result a reader would rather than raw Markdown
source with its `**`/`#`/`[text](url)` syntax still showing.

`nh3` (Rust/ammonia bindings) sanitizes the result: markdown.markdown() will
happily pass through raw HTML embedded in the source, and body is authored by
club staff, who aren't a fully trusted boundary for content served straight
into someone else's public website.
"""

import threading

import markdown as _markdown
import nh3
from django.db import connections
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import Truncator
from django.utils.translation import gettext_lazy as _

_EXTENSIONS = [
    "nl2br",  # staff type in a plain textarea -- a single Enter should break the line,
    # not require a blank line like standard Markdown paragraphs do.
    "sane_lists",
    "fenced_code",
]

_ALLOWED_TAGS = {"p", "br", "strong", "em", "b", "i", "u", "a", "ul", "ol", "li", "blockquote", "code", "pre", "h2", "h3", "h4", "img", "hr"}
_ALLOWED_ATTRIBUTES = {"a": {"href", "title"}, "img": {"src", "alt", "title"}}
_ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def render_body_html(body: str) -> str:
    """Markdown source -> sanitized HTML."""
    html = _markdown.markdown(body, extensions=_EXTENSIONS)
    return nh3.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES, url_schemes=_ALLOWED_URL_SCHEMES)


def render_body_excerpt(body: str, *, words: int) -> str:
    """Plain-text excerpt, derived from the rendered HTML rather than the raw
    Markdown source -- otherwise syntax like `**`/`#`/`[text](url)` shows up
    verbatim in what's meant to be a short teaser."""
    plain_text = strip_tags(render_body_html(body))
    return Truncator(plain_text).words(words, truncate=" …")


#: Notification body is a one-line teaser in the inbox row and a push toast,
#: not a place to read the article -- much shorter than api.py's own
#: EXCERPT_WORDS=40 card teaser.
_NOTIFICATION_EXCERPT_WORDS = 15


def _notify_audience(news_item):
    """Every current-season, active member linked to this news item -- via its
    teams if it has any, or every active member if it's club-wide. Guardians
    are never part of this set themselves (notifications.services.recipient_emails
    resolves them per member); a guardian ClubMembership has kind=GUARDIAN and
    is excluded the same way teams.services.eligible_roster_members excludes
    it from a roster."""
    from club.models import ClubMembership
    from club.services.access import current_season
    from members.models import Member

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


def _dedupe_by_recipients(members):
    """Collapses siblings (or anyone else sharing a guardian) down to one
    notification each, keyed on where the email would actually land -- not
    family membership itself, since a blended family's kids don't
    necessarily share the exact same guardian set, only some overlap. A
    parent of three kids all on the news audience gets one email, not three;
    each kid still gets their own row (and read state) via events.services.
    notifications.notify_new_event, which is deliberately untouched by this --
    an event needs a reply per child, a news post doesn't. Members nobody's
    reachable for (no login, no guardian) are never deduped against
    anything -- there's no shared inbox to spare."""
    from notifications.services import recipient_emails

    seen_emails = set()
    representatives = []
    for member in members:
        emails = recipient_emails(member)
        if emails and any(email in seen_emails for email in emails):
            continue
        representatives.append(member)
        seen_emails.update(emails)
    return representatives


def send_publish_notification(news_item):
    """Notifies this news item's audience that it's live -- called from two places:
    news.management.commands.notify_published_news's periodic sweep (a news item
    scheduled ahead of time, see that command's own docstring), and
    dispatch_send_publish_notification below (an immediate "publish now" -- see its
    own docstring for why that one doesn't just wait for the next sweep). Neither
    publish()/unpublish() nor management.views.NewsPublishView call this directly;
    same reasoning as notify_editors_of_pending_review below. Returns the list of
    Notification rows created (possibly empty). Caller's responsibility to set
    News.notified_at afterwards -- this function only sends, doesn't mark done."""
    from notifications.services import notify_members

    from .models import News

    if news_item.visibility == News.Visibility.EXTERNAL:
        # External-only news never shows up in mobile's own home/list views
        # (mobile.views.HomeView/NewsListView both filter to
        # visibility__in=[INTERNAL, BOTH] -- the opposite of api.py's
        # _visible_news, which is the public *website* feed), so a push/inbox
        # notification for one would point members at an article they'd have
        # no way to find or tap into from the app.
        return []

    members = _dedupe_by_recipients(_notify_audience(news_item))
    if not members:
        return []

    return notify_members(members, club=news_item.club, title=news_item.title, body=render_body_excerpt(news_item.body, words=_NOTIFICATION_EXCERPT_WORDS), source=news_item)


def _send_and_mark_notified(news_id):
    """The actual work behind dispatch_send_publish_notification below, pulled out as its
    own plain function -- same shape as events.services.notifications' own notify_new_event/
    dispatch_notify_new_event split -- so a test can run it synchronously via
    ``mock.patch(..., side_effect=_send_and_mark_notified)`` instead of racing a real
    background thread."""
    from .models import News

    news_item = News.objects.filter(pk=news_id).select_related("club").prefetch_related("teams").first()
    if news_item is None:
        return
    send_publish_notification(news_item)
    news_item.notified_at = timezone.now()
    news_item.save(update_fields=["notified_at"])


def dispatch_send_publish_notification(news_id):
    """Runs _send_and_mark_notified on a daemon background thread, for management.views.
    NewsPublishView.form_valid's "publish now" case specifically -- a news item with no
    future published_at has nothing for notify_published_news's periodic sweep to catch
    for up to 15 minutes, and the common "publish now, notify members" case reads as
    broken if the audience doesn't hear about it until then. A genuinely scheduled-ahead
    item (published_at in the future) skips this entirely and lets the sweep handle it
    once due -- see the view itself for which case is which.

    connections.close_all() in the finally is load-bearing, same reasoning as
    events.services.notifications.dispatch_notify_new_event's own: a manually-
    spawned thread doesn't get Django's usual per-request connection teardown."""

    def _run():
        try:
            _send_and_mark_notified(news_id)
        finally:
            connections.close_all()

    threading.Thread(target=_run, daemon=True).start()


def notify_editors_of_pending_review(news_item):
    """In-app only (see notifications.services.notify_members's send_email
    param) -- a review queue that emailed every editor/admin on every
    submission would get noisy fast; the topbar bell and the dashboard card
    are enough for this. Called from management.views.NewsSubmitForReviewView,
    not from News.submit_for_review() itself, same as publish()/unpublish()
    never send anything on their own either."""
    from club.models import ClubRole
    from members.models import Member
    from notifications.services import notify_members

    editors = Member.objects.filter(roles__club=news_item.club, roles__role__in=[ClubRole.Roles.ADMIN, ClubRole.Roles.EDITOR]).distinct()
    title = _("“%(news)s” is ready for review") % {"news": news_item.title}
    body = _("%(author)s submitted this news item for review before it can go live.") % {"author": news_item.created_by or _("Someone")}
    return notify_members(editors, club=news_item.club, title=title, body=body, source=news_item, send_email=False)
