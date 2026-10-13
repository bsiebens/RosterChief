import datetime
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from appapi.models import PushDevice
from appapi.push import PushGateway
from authentication.models import User
from club.models import Club, ClubRole
from members.models import Member
from mobile.models import PushSubscription
from notifications.models import Notification, NotificationCategory

from .models import Announcement, AnnouncementSeen
from .services import audience_member_count, cancel, consume_for, create_and_confirm, publish


def make_subscription(club, member, endpoint):
    return PushSubscription.objects.create(club=club, member=member, endpoint=endpoint, p256dh="key", auth="auth")


class AnnouncementServiceTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United")
        cls.other_club = Club.objects.create(name="Feyenoord")
        cls.superuser = User.objects.create_user(email="root@example.com", password="pw-secret-123", is_staff=True, is_superuser=True)


def make_account_member(club, email, *, role=ClubRole.Roles.MEMBER, first_name="Jane"):
    """A member with a login, attached to ``club`` through a club role (one of the ways
    announcements.services.audience_members recognises attachment)."""
    user = User.objects.create_user(email=email, password="pw-secret-123")
    member = Member.objects.create(first_name=first_name, last_name="Doe", user=user)
    ClubRole.objects.create(club=club, member=member, role=role)
    return member


class CreateAndConfirmTests(AnnouncementServiceTestBase):
    def test_no_schedule_publishes_immediately(self):
        announcement = create_and_confirm(title="Heads up", message="Maintenance tonight.", club=None, scheduled_for=None, created_by=self.superuser)

        self.assertEqual(announcement.status, Announcement.Status.SENT)
        self.assertIsNotNone(announcement.sent_at)

    def test_a_past_schedule_publishes_immediately_too(self):
        announcement = create_and_confirm(title="Heads up", message="Maintenance tonight.", club=None, scheduled_for=timezone.now() - datetime.timedelta(minutes=1), created_by=self.superuser)

        self.assertEqual(announcement.status, Announcement.Status.SENT)

    def test_a_future_schedule_stays_pending(self):
        make_account_member(self.club, "jane@example.com")
        announcement = create_and_confirm(title="Heads up", message="Maintenance tonight.", club=None, scheduled_for=timezone.now() + datetime.timedelta(days=1), created_by=self.superuser)

        self.assertEqual(announcement.status, Announcement.Status.PENDING)
        self.assertIsNone(announcement.sent_at)
        self.assertFalse(Notification.objects.exists())


class RecordingPublisher(PushGateway):
    sent: list = []

    def publish(self, device, *, title, body, data, badge=None, thread_id=None):
        RecordingPublisher.sent.append((device.installation_id, title, body))
        return True


class PublishTests(AnnouncementServiceTestBase):
    def test_publish_creates_an_inbox_notification_per_account_holding_member(self):
        jane = make_account_member(self.club, "jane@example.com")
        john = make_account_member(self.club, "john@example.com", first_name="John")
        make_account_member(self.other_club, "other@example.com", first_name="Other")
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=self.club)

        publish(announcement)

        notifications = Notification.objects.all()
        self.assertEqual({n.member_id for n in notifications}, {jane.pk, john.pk})
        for notification in notifications:
            self.assertEqual((notification.club, notification.title, notification.body, notification.source), (self.club, "Heads up", "Body", None))
            self.assertEqual(notification.category, NotificationCategory.ANNOUNCEMENT)

    def test_a_member_without_a_login_gets_nothing(self):
        member = Member.objects.create(first_name="No", last_name="Login")
        ClubRole.objects.create(club=self.club, member=member)
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=self.club)

        publish(announcement)

        self.assertFalse(Notification.objects.exists())

    def test_a_member_not_attached_to_the_club_gets_nothing(self):
        user = User.objects.create_user(email="loose@example.com", password="pw-secret-123")
        Member.objects.create(first_name="Loose", last_name="End", user=user)
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=self.club)

        publish(announcement)

        self.assertFalse(Notification.objects.exists())

    def test_platform_wide_covers_every_club(self):
        jane = make_account_member(self.club, "jane@example.com")
        other = make_account_member(self.other_club, "other@example.com", first_name="Other")
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=None)

        publish(announcement)

        self.assertEqual({(n.club_id, n.member_id) for n in Notification.objects.all()}, {(self.club.pk, jane.pk), (self.other_club.pk, other.pk)})

    def test_someone_in_two_clubs_gets_one_notification_per_club_on_a_platform_wide_announcement(self):
        jane = make_account_member(self.club, "jane@example.com")
        ClubRole.objects.create(club=self.other_club, member=jane)
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=None)

        publish(announcement)

        self.assertEqual(Notification.objects.filter(member=jane).count(), 2)
        self.assertEqual(audience_member_count(None), 1)

    def test_publishing_twice_only_notifies_once(self):
        make_account_member(self.club, "jane@example.com")
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=None)

        publish(announcement)
        publish(announcement)

        self.assertEqual(Notification.objects.count(), 1)

    def test_the_announcement_is_emailed(self):
        make_account_member(self.club, "jane@example.com")
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=self.club)

        publish(announcement)

        self.assertEqual([message.to for message in mail.outbox], [["jane@example.com"]])
        self.assertIn("Heads up", mail.outbox[0].subject)

    @override_settings(APP_PUSH_GATEWAY="announcements.tests.RecordingPublisher", VAPID_PRIVATE_KEY="test-private-key", VAPID_ADMIN_EMAIL="admin@example.com")
    def test_a_member_with_the_native_app_gets_native_push_and_no_web_push(self):
        RecordingPublisher.sent = []
        jane = make_account_member(self.club, "jane@example.com")
        PushDevice.objects.create(user=jane.user, installation_id="jane-phone", platform="ios", push_token="t")
        make_subscription(self.club, jane, "https://fcm.googleapis.com/fcm/send/1")
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=self.club)

        with mock.patch("mobile.services.push.webpush") as webpush:
            publish(announcement)

        self.assertEqual(RecordingPublisher.sent, [("jane-phone", "Heads up", "Body")])
        webpush.assert_not_called()

    @override_settings(VAPID_PRIVATE_KEY="test-private-key", VAPID_ADMIN_EMAIL="admin@example.com")
    def test_a_member_with_only_a_web_subscription_gets_web_push(self):
        jane = make_account_member(self.club, "jane@example.com")
        make_subscription(self.club, jane, "https://fcm.googleapis.com/fcm/send/1")
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=self.club)

        with mock.patch("mobile.services.push.webpush") as webpush:
            publish(announcement)

        webpush.assert_called_once()

    def test_a_cancelled_announcement_cannot_be_published(self):
        make_account_member(self.club, "jane@example.com")
        announcement = Announcement.objects.create(title="Heads up", message="Body", status=Announcement.Status.CANCELLED)

        publish(announcement)

        announcement.refresh_from_db()
        self.assertEqual(announcement.status, Announcement.Status.CANCELLED)
        self.assertFalse(Notification.objects.exists())


class AudienceCountTests(AnnouncementServiceTestBase):
    def test_counts_distinct_account_holders_in_scope(self):
        jane = make_account_member(self.club, "jane@example.com")
        ClubRole.objects.create(club=self.other_club, member=jane)
        make_account_member(self.club, "john@example.com", first_name="John")
        Member.objects.create(first_name="No", last_name="Login")

        self.assertEqual(audience_member_count(self.club), 2)
        self.assertEqual(audience_member_count(self.other_club), 1)
        self.assertEqual(audience_member_count(None), 2)


class CancelTests(AnnouncementServiceTestBase):
    def test_cancelling_a_pending_announcement(self):
        announcement = Announcement.objects.create(title="Heads up", message="Body")

        cancel(announcement)

        announcement.refresh_from_db()
        self.assertEqual(announcement.status, Announcement.Status.CANCELLED)

    def test_cancelling_an_already_sent_announcement_is_a_no_op(self):
        announcement = Announcement.objects.create(title="Heads up", message="Body", status=Announcement.Status.SENT, sent_at=timezone.now())

        cancel(announcement)

        announcement.refresh_from_db()
        self.assertEqual(announcement.status, Announcement.Status.SENT)


class ConsumeForTests(AnnouncementServiceTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")

    def test_a_live_announcement_is_returned_once(self):
        announcement = Announcement.objects.create(title="Heads up", message="Body", status=Announcement.Status.SENT, sent_at=timezone.now())

        first = consume_for(self.user, self.club)
        second = consume_for(self.user, self.club)

        self.assertEqual(first, announcement)
        self.assertIsNone(second)
        self.assertEqual(AnnouncementSeen.objects.filter(announcement=announcement, user=self.user).count(), 1)

    def test_a_club_targeted_announcement_is_invisible_to_another_club(self):
        Announcement.objects.create(title="Heads up", message="Body", club=self.other_club, status=Announcement.Status.SENT, sent_at=timezone.now())

        self.assertIsNone(consume_for(self.user, self.club))

    def test_a_platform_wide_announcement_reaches_every_club(self):
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=None, status=Announcement.Status.SENT, sent_at=timezone.now())

        self.assertEqual(consume_for(self.user, self.club), announcement)

    def test_a_pending_announcement_is_not_shown_yet(self):
        Announcement.objects.create(title="Heads up", message="Body", status=Announcement.Status.PENDING)

        self.assertIsNone(consume_for(self.user, self.club))

    def test_different_users_each_see_it_once(self):
        other_user = User.objects.create_user(email="other@example.com", password="pw-secret-123")
        announcement = Announcement.objects.create(title="Heads up", message="Body", status=Announcement.Status.SENT, sent_at=timezone.now())

        self.assertEqual(consume_for(self.user, self.club), announcement)
        self.assertEqual(consume_for(other_user, self.club), announcement)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class PendingAnnouncementViewTests(AnnouncementServiceTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")

    def _get(self):
        return self.client.get(reverse("announcement_pending"), HTTP_HOST="ajax-united.rosterchief.app")

    def test_anonymous_gets_nothing(self):
        response = self._get()

        self.assertEqual(response.json(), {})

    def test_a_live_announcement_is_returned_as_json_and_only_once(self):
        Announcement.objects.create(title="Heads up", message="Body", status=Announcement.Status.SENT, sent_at=timezone.now())
        self.client.force_login(self.user)

        first = self._get()
        second = self._get()

        self.assertEqual(first.json(), {"title": "Heads up", "message": "Body"})
        self.assertEqual(second.json(), {})

    def test_nothing_live_returns_an_empty_object(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.json(), {})


class PublishScheduledAnnouncementsCommandTests(AnnouncementServiceTestBase):
    def test_a_due_scheduled_announcement_is_published(self):
        due = Announcement.objects.create(title="Due", message="Body", scheduled_for=timezone.now() - datetime.timedelta(minutes=1))
        not_due = Announcement.objects.create(title="Not due", message="Body", scheduled_for=timezone.now() + datetime.timedelta(days=1))

        call_command("publish_scheduled_announcements")

        due.refresh_from_db()
        not_due.refresh_from_db()
        self.assertEqual(due.status, Announcement.Status.SENT)
        self.assertEqual(not_due.status, Announcement.Status.PENDING)
