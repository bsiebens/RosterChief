"""Creating, publishing and consuming platform announcements.

Publishing goes through the notification dispatcher (``notifications.services.notify_members``):
every person in scope gets an inbox row, and the dispatcher fans it out to every channel
(native push, web push, email) with its own de-duplication rules.
"""

from dataclasses import dataclass

from django.db.models import Q
from django.utils import timezone

from club.models import Club, ClubRole
from members.models import FamilyMembership, Member
from notifications.dispatch import dispatch_to_user
from notifications.models import Notification, NotificationCategory
from notifications.services import _build_message, _render_email, notify_members
from rosterchief.mail import send_message

from .models import Announcement, AnnouncementSeen, live_announcements_query

#: Members per notify_members call -- keeps one bulk insert / one dispatch batch reasonable.
BATCH_SIZE = 500


def audience_members(club, audience=Announcement.Audience.EVERYONE):
    """The members of ``club`` an announcement reaches: everyone who holds a login
    (``Member.user`` set) and is attached to the club in any way, in any season -- a club
    membership row, a team membership, a staff assignment or a club role (mirrors
    ``members.services.photos._attached_to_club``) -- narrowed by ``audience`` (definitions in
    Announcement's docstring). Members without an account are skipped (nobody to show an
    inbox row or push to)."""
    attached = Q(member_of__club=club) | Q(team_memberships__team__club=club) | Q(staff_assignments__team__club=club) | Q(roles__club=club)
    members = Member.objects.filter(user__isnull=False).filter(attached)
    today = timezone.localdate()
    if audience == Announcement.Audience.ADMINS:
        members = members.filter(roles__club=club, roles__role__in=[ClubRole.Roles.ADMIN, ClubRole.Roles.EDITOR, ClubRole.Roles.MEMBER_ADMIN])
    elif audience == Announcement.Audience.STAFF:
        members = members.filter(staff_assignments__team__club=club, staff_assignments__season__start_date__lte=today, staff_assignments__season__end_date__gte=today)
    elif audience == Announcement.Audience.MEMBERS:
        members = members.filter(roles__club=club, roles__role=ClubRole.Roles.MEMBER)
    elif audience == Announcement.Audience.PARENTS:
        members = members.filter(family_memberships__role__in=[FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN])
    elif audience == Announcement.Audience.PLAYERS:
        members = members.filter(team_memberships__team__club=club, team_memberships__season__start_date__lte=today, team_memberships__season__end_date__gte=today)
    return members.distinct()


def audience_by_club(club, audience=Announcement.Audience.EVERYONE) -> list:
    """``[(club, [members])]`` -- what publish actually delivers. For one club, just its
    audience. For ``club=None`` each person appears exactly once, under the first club (by
    ``Club.created``, then pk) whose audience contains them, so someone in several clubs gets
    one notification, not one per club."""
    if club is not None:
        return [(club, list(audience_members(club, audience).select_related("user").order_by("pk")))]

    seen: set = set()
    result = []
    for each in Club.objects.order_by("created", "pk"):
        members = [member for member in audience_members(each, audience).select_related("user").order_by("pk") if member.pk not in seen]
        seen.update(member.pk for member in members)
        if members:
            result.append((each, members))
    return result


def audience_member_count(club, audience=Announcement.Audience.EVERYONE) -> int:
    """For the confirm screen -- how many distinct people with an account an announcement
    for ``club`` (or every club, if None) and ``audience`` reaches; exactly the number of
    notifications ``publish`` creates."""
    return sum(len(members) for _club, members in audience_by_club(club, audience))


def publish(announcement: Announcement) -> Announcement:
    """Make an announcement live: flips it to SENT and notifies every account-holding
    member in scope (inbox row + every channel, see ``audience_by_club``). Idempotent -- a
    PENDING check guards both the immediate path (create_and_confirm calling straight
    through) and the scheduled job (which only selects PENDING, due rows) from ever
    double-sending the same announcement."""
    if announcement.status != Announcement.Status.PENDING:
        return announcement

    announcement.status = Announcement.Status.SENT
    announcement.sent_at = timezone.now()
    announcement.save(update_fields=["status", "sent_at", "modified"])

    for club, members in audience_by_club(announcement.club, announcement.audience):
        for start in range(0, len(members), BATCH_SIZE):
            notify_members(members[start : start + BATCH_SIZE], club=club, title=announcement.title, body=announcement.message, send_email=True, category=NotificationCategory.ANNOUNCEMENT)

    return announcement


@dataclass
class TestSendResult:
    email: bool
    #: Accounts-channel deliveries reached; None when no push was attempted (no club to push in).
    pushed: int | None


def send_test(user, *, title: str, message: str) -> TestSendResult:
    """Try an announcement on ``user`` alone: the notification email (same templates, subject
    prefixed "[TEST]") to their address and a push to their own devices. Creates no
    Announcement and no Notification; nobody else is contacted. Push needs a club: the first
    club (by Club.created, then pk) the user's member belongs to; without one it is skipped."""
    member = Member.objects.filter(user=user).first()
    club = None
    if member is not None:
        club = next((each for each in Club.objects.order_by("created", "pk") if audience_members(each).filter(pk=member.pk).exists()), None)

    notification = Notification(club=club or Club(name="RosterChief"), member=member, title=title, body=message, category=NotificationCategory.ANNOUNCEMENT)
    subject, text_body, html_body = _render_email(notification)
    email = send_message(_build_message(f"[TEST] {subject}", text_body, html_body, user.email))

    pushed = None
    if club is not None:
        data = {"club_slug": club.slug, "kind": "announcement", "category": NotificationCategory.ANNOUNCEMENT}
        pushed = dispatch_to_user(club=club, user=user, texts=lambda _language: (f"[TEST] {title}", message), data=data, url="/app/", category=NotificationCategory.ANNOUNCEMENT).total
    return TestSendResult(email=email, pushed=pushed)


def create_and_confirm(*, title: str, message: str, club, scheduled_for, created_by, audience=Announcement.Audience.EVERYONE) -> Announcement:
    """The only place an Announcement row gets created -- always from the confirm
    step of the compose flow (announcements.views), never the compose form
    itself, so there is no unconfirmed/draft row a superuser could accidentally
    leave lying around half-set-up. Publishes immediately when nothing (or a
    past/now time) was scheduled; otherwise leaves it PENDING for
    announcements.management.commands.publish_scheduled_announcements."""
    announcement = Announcement.objects.create(title=title, message=message, club=club, audience=audience, scheduled_for=scheduled_for, created_by=created_by)
    if announcement.is_due:
        publish(announcement)

    return announcement


def cancel(announcement: Announcement) -> Announcement:
    """Only meaningful for a still-PENDING, not-yet-due scheduled announcement --
    a no-op once it's already SENT (there's nothing left to stop) or CANCELLED."""
    if announcement.status == Announcement.Status.PENDING:
        announcement.status = Announcement.Status.CANCELLED
        announcement.save(update_fields=["status", "modified"])

    return announcement


def consume_for(user, club) -> list[Announcement]:
    """Every live announcement this user/club hasn't been shown yet, newest first,
    marking each one shown in the same call -- an empty list the moment there's
    nothing new. Already-seen ones are excluded in the query itself, so the common
    case (nothing pending) costs a single query. An announcement that some other
    concurrent request (e.g. a second open tab) already won the race to mark seen
    is left out too. See AnnouncementSeen's own docstring for why ``created`` is
    what decides that, not a preceding existence check."""
    unseen = Announcement.objects.filter(live_announcements_query(club)).exclude(seen_by__user=user).order_by("-sent_at")
    shown = []
    for candidate in unseen:
        # An announcement for an audience (coaches, parents, ...) only pops up for people in it,
        # the same people publish() sent it to.
        if candidate.audience != Announcement.Audience.EVERYONE and not audience_members(club, candidate.audience).filter(user=user).exists():
            continue
        _seen, created = AnnouncementSeen.objects.get_or_create(announcement=candidate, user=user)
        if created:
            shown.append(candidate)
    return shown
