"""Creating, publishing and consuming platform announcements.

Directly imports mobile.models/mobile.services.push rather than going through a
signal the way mobile.signals listens for notifications.Notification -- unlike that
generic, channel-agnostic activity log, an Announcement's entire reason to exist
*is* "push this to the PWA", so there is no abstraction to protect by not naming
the channel here.
"""

from django.utils import timezone

from members.models import Member
from mobile.models import PushSubscription
from mobile.services.push import send_push_to_member

from .models import Announcement, AnnouncementSeen, live_announcements_query


def audience_subscriptions(club):
    """Every PushSubscription an announcement targeted at ``club`` (or every
    club, if ``club`` is None) should reach."""
    subscriptions = PushSubscription.objects.all()
    if club is not None:
        subscriptions = subscriptions.filter(club=club)
    return subscriptions


def audience_member_count(club) -> int:
    """For the confirm screen -- how many distinct people currently have at
    least one subscribed device in scope. Not a promise of delivery (a stale
    or unreachable subscription still counts), just a sense of reach."""
    return audience_subscriptions(club).values("member_id").distinct().count()


def publish(announcement: Announcement) -> Announcement:
    """Make an announcement live: flips it to SENT and pushes it to every
    subscribed device in scope. Idempotent -- a PENDING check guards both the
    immediate path (create_and_confirm calling straight through) and the
    scheduled job (which only selects PENDING, due rows) from ever double-
    sending the same announcement."""
    if announcement.status != Announcement.Status.PENDING:
        return announcement

    announcement.status = Announcement.Status.SENT
    announcement.sent_at = timezone.now()
    announcement.save(update_fields=["status", "sent_at", "modified"])

    # One send per member, not per subscription -- send_push_to_member already fans
    # out to every one of that member's own subscribed devices on its own.
    member_ids = audience_subscriptions(announcement.club).values_list("member_id", flat=True).distinct()
    for member in Member.objects.filter(id__in=member_ids):
        send_push_to_member(member, title=announcement.title, body=announcement.message)

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
