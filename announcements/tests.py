import datetime
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from authentication.models import User
from club.models import Club
from members.models import Member
from mobile.models import PushSubscription

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


@mock.patch("announcements.services.send_push_to_member")
class CreateAndConfirmTests(AnnouncementServiceTestBase):
    def test_no_schedule_publishes_immediately(self, send_push):
        announcement = create_and_confirm(title="Heads up", message="Maintenance tonight.", club=None, scheduled_for=None, created_by=self.superuser)

        self.assertEqual(announcement.status, Announcement.Status.SENT)
        self.assertIsNotNone(announcement.sent_at)

    def test_a_past_schedule_publishes_immediately_too(self, send_push):
        announcement = create_and_confirm(title="Heads up", message="Maintenance tonight.", club=None, scheduled_for=timezone.now() - datetime.timedelta(minutes=1), created_by=self.superuser)

        self.assertEqual(announcement.status, Announcement.Status.SENT)

    def test_a_future_schedule_stays_pending(self, send_push):
        announcement = create_and_confirm(title="Heads up", message="Maintenance tonight.", club=None, scheduled_for=timezone.now() + datetime.timedelta(days=1), created_by=self.superuser)

        self.assertEqual(announcement.status, Announcement.Status.PENDING)
        self.assertIsNone(announcement.sent_at)
        send_push.assert_not_called()


@mock.patch("announcements.services.send_push_to_member")
class PublishTests(AnnouncementServiceTestBase):
    def test_publish_pushes_to_every_member_with_a_subscription(self, send_push):
        member = Member.objects.create(first_name="Jane", last_name="Doe")
        make_subscription(self.club, member, "https://push.example/1")
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=None)

        publish(announcement)

        send_push.assert_called_once_with(member, title="Heads up", body="Body")

    def test_one_send_per_member_even_with_multiple_devices(self, send_push):
        member = Member.objects.create(first_name="Jane", last_name="Doe")
        make_subscription(self.club, member, "https://push.example/1")
        make_subscription(self.club, member, "https://push.example/2")
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=None)

        publish(announcement)

        self.assertEqual(send_push.call_count, 1)

    def test_club_targeted_announcement_only_reaches_that_clubs_subscribers(self, send_push):
        in_club = Member.objects.create(first_name="In", last_name="Club")
        other_club_member = Member.objects.create(first_name="Other", last_name="Club")
        make_subscription(self.club, in_club, "https://push.example/1")
        make_subscription(self.other_club, other_club_member, "https://push.example/2")
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=self.club)

        publish(announcement)

        send_push.assert_called_once_with(in_club, title="Heads up", body="Body")

    def test_publishing_twice_only_sends_once(self, send_push):
        member = Member.objects.create(first_name="Jane", last_name="Doe")
        make_subscription(self.club, member, "https://push.example/1")
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=None)

        publish(announcement)
        publish(announcement)

        self.assertEqual(send_push.call_count, 1)

    def test_a_cancelled_announcement_cannot_be_published(self, send_push):
        announcement = Announcement.objects.create(title="Heads up", message="Body", status=Announcement.Status.CANCELLED)

        publish(announcement)

        announcement.refresh_from_db()
        self.assertEqual(announcement.status, Announcement.Status.CANCELLED)
        send_push.assert_not_called()


class AudienceCountTests(AnnouncementServiceTestBase):
    def test_counts_distinct_members_in_scope(self):
        member = Member.objects.create(first_name="Jane", last_name="Doe")
        make_subscription(self.club, member, "https://push.example/1")
        make_subscription(self.club, member, "https://push.example/2")

        self.assertEqual(audience_member_count(self.club), 1)
        self.assertEqual(audience_member_count(None), 1)

    def test_a_different_clubs_subscriptions_are_excluded(self):
        member = Member.objects.create(first_name="Jane", last_name="Doe")
        make_subscription(self.other_club, member, "https://push.example/1")

        self.assertEqual(audience_member_count(self.club), 0)


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
    @mock.patch("announcements.services.send_push_to_member")
    def test_a_due_scheduled_announcement_is_published(self, send_push):
        due = Announcement.objects.create(title="Due", message="Body", scheduled_for=timezone.now() - datetime.timedelta(minutes=1))
        not_due = Announcement.objects.create(title="Not due", message="Body", scheduled_for=timezone.now() + datetime.timedelta(days=1))

        call_command("publish_scheduled_announcements")

        due.refresh_from_db()
        not_due.refresh_from_db()
        self.assertEqual(due.status, Announcement.Status.SENT)
        self.assertEqual(not_due.status, Announcement.Status.PENDING)
