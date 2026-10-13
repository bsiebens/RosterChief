"""Creating, publishing and consuming platform announcements.

Publishing goes through the notification dispatcher (``notifications.services.notify_members``):
every person in scope gets an inbox row, and the dispatcher fans it out to every channel
(native push, web push, email) with its own de-duplication rules.
"""

from django.db.models import Q
from django.utils import timezone

from club.models import Club
from members.models import Member
from notifications.services import notify_members

from .models import Announcement, AnnouncementSeen, live_announcements_query

#: Members per notify_members call -- keeps one bulk insert / one dispatch batch reasonable.
BATCH_SIZE = 500


def audience_members(club):
    """The members of ``club`` an announcement reaches: everyone who holds a login
    (``Member.user`` set) and is attached to the club in any way, in any season -- a club
    membership row, a team membership, a staff assignment or a club role. Mirrors
    ``members.services.photos._attached_to_club``. Members without an account are skipped
    (nobody to show an inbox row or push to)."""
    attached = Q(member_of__club=club) | Q(team_memberships__team__club=club) | Q(staff_assignments__team__club=club) | Q(roles__club=club)
    return Member.objects.filter(user__isnull=False).filter(attached).distinct()


def audience_member_count(club) -> int:
    """For the confirm screen -- how many distinct people with an account an announcement
    targeted at ``club`` (or every club, if None) reaches. A person in two clubs counts once
    here, but gets one notification per club on an all-club announcement."""
    if club is not None:
        return audience_members(club).count()
    return Member.objects.filter(pk__in=[pk for each in Club.objects.all() for pk in audience_members(each).values_list("pk", flat=True)]).count()


def publish(announcement: Announcement) -> Announcement:
    """Make an announcement live: flips it to SENT and notifies every account-holding
    member in scope (inbox row + every channel, see ``audience_members``). Idempotent -- a
    PENDING check guards both the immediate path (create_and_confirm calling straight
    through) and the scheduled job (which only selects PENDING, due rows) from ever
    double-sending the same announcement."""
    if announcement.status != Announcement.Status.PENDING:
        return announcement

    announcement.status = Announcement.Status.SENT
    announcement.sent_at = timezone.now()
    announcement.save(update_fields=["status", "sent_at", "modified"])

    clubs = [announcement.club] if announcement.club is not None else list(Club.objects.all())
    for club in clubs:
        members = list(audience_members(club).select_related("user").order_by("pk"))
        for start in range(0, len(members), BATCH_SIZE):
            notify_members(members[start : start + BATCH_SIZE], club=club, title=announcement.title, body=announcement.message, send_email=True)

    return announcement


def create_and_confirm(*, title: str, message: str, club, scheduled_for, created_by) -> Announcement:
    """The only place an Announcement row gets created -- always from the confirm
    step of the compose flow (announcements.views), never the compose form
    itself, so there is no unconfirmed/draft row a superuser could accidentally
    leave lying around half-set-up. Publishes immediately when nothing (or a
    past/now time) was scheduled; otherwise leaves it PENDING for
    announcements.management.commands.publish_scheduled_announcements."""
    announcement = Announcement.objects.create(title=title, message=message, club=club, scheduled_for=scheduled_for, created_by=created_by)
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


def consume_for(user, club) -> Announcement | None:
    """The one live, not-yet-shown announcement for this user/club, marking it
    shown in the same call -- returns None the moment there's nothing new, or
    once some other concurrent request (e.g. a second open tab) already won the
    race to mark it seen first. See AnnouncementSeen's own docstring for why
    ``created`` is what decides that, not a preceding existence check."""
    announcement = Announcement.objects.filter(live_announcements_query(club)).order_by("-sent_at").first()
    if announcement is None:
        return None

    _seen, created = AnnouncementSeen.objects.get_or_create(announcement=announcement, user=user)
    return announcement if created else None
