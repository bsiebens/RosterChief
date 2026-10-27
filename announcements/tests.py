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
from club.models import Club, ClubRole, Season
from members.models import Family, FamilyMembership, Member
from mobile.models import PushSubscription
from notifications.models import Notification, NotificationCategory
from teams.models import Position, StaffAssignment, Team, TeamMembership

from .models import Announcement, AnnouncementSeen
from .services import audience_member_count, audience_members, cancel, consume_for, create_and_confirm, publish, send_test


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

    def publish(self, device, *, title, body, data, badge=None, thread_id=None, policy=None, collapse_id=None):
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

    def test_someone_in_two_clubs_gets_exactly_one_notification_on_a_platform_wide_announcement(self):
        jane = make_account_member(self.club, "jane@example.com")
        ClubRole.objects.create(club=self.other_club, member=jane)
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=None)

        publish(announcement)

        notifications = Notification.objects.filter(member=jane)
        self.assertEqual(notifications.count(), 1)
        self.assertEqual(notifications.get().club, self.club)  # the earlier-created club
        self.assertEqual(audience_member_count(None), 1)
        self.assertEqual(audience_member_count(None), Notification.objects.count())

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

        self.assertEqual(first, [announcement])
        self.assertEqual(second, [])
        self.assertEqual(AnnouncementSeen.objects.filter(announcement=announcement, user=self.user).count(), 1)

    def test_every_unseen_announcement_is_returned_newest_first_and_an_older_one_is_not_lost(self):
        now = timezone.now()
        older = Announcement.objects.create(title="Older", message="Body", status=Announcement.Status.SENT, sent_at=now - datetime.timedelta(hours=2))
        newer = Announcement.objects.create(title="Newer", message="Body", status=Announcement.Status.SENT, sent_at=now - datetime.timedelta(hours=1))
        AnnouncementSeen.objects.create(announcement=newer, user=self.user)

        self.assertEqual(consume_for(self.user, self.club), [older])

    def test_several_unseen_announcements_come_back_together(self):
        now = timezone.now()
        older = Announcement.objects.create(title="Older", message="Body", status=Announcement.Status.SENT, sent_at=now - datetime.timedelta(hours=2))
        newer = Announcement.objects.create(title="Newer", message="Body", status=Announcement.Status.SENT, sent_at=now - datetime.timedelta(hours=1))

        self.assertEqual(consume_for(self.user, self.club), [newer, older])
        self.assertEqual(consume_for(self.user, self.club), [])

    def test_a_club_targeted_announcement_is_invisible_to_another_club(self):
        Announcement.objects.create(title="Heads up", message="Body", club=self.other_club, status=Announcement.Status.SENT, sent_at=timezone.now())

        self.assertEqual(consume_for(self.user, self.club), [])

    def test_a_platform_wide_announcement_reaches_every_club(self):
        announcement = Announcement.objects.create(title="Heads up", message="Body", club=None, status=Announcement.Status.SENT, sent_at=timezone.now())

        self.assertEqual(consume_for(self.user, self.club), [announcement])

    def test_a_pending_announcement_is_not_shown_yet(self):
        Announcement.objects.create(title="Heads up", message="Body", status=Announcement.Status.PENDING)

        self.assertEqual(consume_for(self.user, self.club), [])

    def test_different_users_each_see_it_once(self):
        other_user = User.objects.create_user(email="other@example.com", password="pw-secret-123")
        announcement = Announcement.objects.create(title="Heads up", message="Body", status=Announcement.Status.SENT, sent_at=timezone.now())

        self.assertEqual(consume_for(self.user, self.club), [announcement])
        self.assertEqual(consume_for(other_user, self.club), [announcement])


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

        self.assertEqual(response.json(), {"announcements": []})

    def test_a_live_announcement_is_returned_as_json_and_only_once(self):
        Announcement.objects.create(title="Heads up", message="Body", status=Announcement.Status.SENT, sent_at=timezone.now())
        self.client.force_login(self.user)

        first = self._get()
        second = self._get()

        self.assertEqual(first.json(), {"announcements": [{"title": "Heads up", "message": "Body"}]})
        self.assertEqual(second.json(), {"announcements": []})

    def test_nothing_live_returns_an_empty_list(self):
        self.client.force_login(self.user)

        response = self._get()

        self.assertEqual(response.json(), {"announcements": []})


class PublishScheduledAnnouncementsCommandTests(AnnouncementServiceTestBase):
    def test_a_due_scheduled_announcement_is_published(self):
        due = Announcement.objects.create(title="Due", message="Body", scheduled_for=timezone.now() - datetime.timedelta(minutes=1))
        not_due = Announcement.objects.create(title="Not due", message="Body", scheduled_for=timezone.now() + datetime.timedelta(days=1))

        call_command("publish_scheduled_announcements")

        due.refresh_from_db()
        not_due.refresh_from_db()
        self.assertEqual(due.status, Announcement.Status.SENT)
        self.assertEqual(not_due.status, Announcement.Status.PENDING)


class AudienceFilterTests(AnnouncementServiceTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=30))
        cls.old_season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=800), end_date=today - datetime.timedelta(days=400))
        cls.team = Team.objects.create(club=cls.club, name="First", short_name="1st")
        cls.forward = Position.objects.create(club=cls.club, name="Forward", short_name="FW")
        cls.coach_position = Position.objects.create(club=cls.club, name="Coach", short_name="HC", staff_position=True)

        cls.admin = make_account_member(cls.club, "admin@example.com", role=ClubRole.Roles.ADMIN, first_name="Admin")
        cls.editor = make_account_member(cls.club, "editor@example.com", role=ClubRole.Roles.EDITOR, first_name="Editor")
        cls.plain = make_account_member(cls.club, "plain@example.com", first_name="Plain")
        cls.player = make_account_member(cls.club, "player@example.com", first_name="Player")
        TeamMembership.objects.create(team=cls.team, member=cls.player, season=cls.season, position=cls.forward)
        cls.ex_player = make_account_member(cls.club, "ex@example.com", first_name="Ex")
        TeamMembership.objects.create(team=cls.team, member=cls.ex_player, season=cls.old_season, position=cls.forward)
        cls.coach = make_account_member(cls.club, "coach@example.com", first_name="Coach")
        StaffAssignment.objects.create(team=cls.team, member=cls.coach, season=cls.season, position=cls.coach_position)
        cls.ex_coach = make_account_member(cls.club, "excoach@example.com", first_name="ExCoach")
        StaffAssignment.objects.create(team=cls.team, member=cls.ex_coach, season=cls.old_season, position=cls.coach_position)
        cls.parent = make_account_member(cls.club, "parent@example.com", first_name="Parent")
        cls.guardian = make_account_member(cls.club, "guardian@example.com", first_name="Guardian")
        cls.sibling = make_account_member(cls.club, "sibling@example.com", first_name="Sibling")
        family = Family.objects.create(club=cls.club, name="Doe")
        FamilyMembership.objects.create(family=family, member=cls.parent, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=cls.guardian, role=FamilyMembership.FamilyRole.GUARDIAN)
        FamilyMembership.objects.create(family=family, member=cls.sibling, role=FamilyMembership.FamilyRole.CHILD)
        cls.other_admin = make_account_member(cls.other_club, "otheradmin@example.com", role=ClubRole.Roles.ADMIN, first_name="OtherAdmin")

    def members(self, club, audience):
        return set(audience_members(club, audience))

    def test_everyone(self):
        self.assertEqual(
            self.members(self.club, Announcement.Audience.EVERYONE),
            {self.admin, self.editor, self.plain, self.player, self.ex_player, self.coach, self.ex_coach, self.parent, self.guardian, self.sibling},
        )

    def test_admins_are_admin_type_club_roles_only(self):
        self.assertEqual(self.members(self.club, Announcement.Audience.ADMINS), {self.admin, self.editor})

    def test_staff_needs_a_current_season_assignment(self):
        self.assertEqual(self.members(self.club, Announcement.Audience.STAFF), {self.coach})

    def test_parents_are_parent_or_guardian_family_members(self):
        self.assertEqual(self.members(self.club, Announcement.Audience.PARENTS), {self.parent, self.guardian})

    def test_players_need_a_current_season_roster_spot(self):
        self.assertEqual(self.members(self.club, Announcement.Audience.PLAYERS), {self.player})

    def test_filter_and_club_combination(self):
        self.assertEqual(self.members(self.other_club, Announcement.Audience.ADMINS), {self.other_admin})
        self.assertEqual(self.members(self.other_club, Announcement.Audience.PLAYERS), set())
        self.assertEqual(audience_member_count(None, Announcement.Audience.ADMINS), 3)
        self.assertEqual(audience_member_count(self.club, Announcement.Audience.ADMINS), 2)

    def test_publish_notifies_only_the_filtered_audience(self):
        publish(Announcement.objects.create(title="Coaches", message="Body", club=None, audience=Announcement.Audience.STAFF))

        self.assertEqual({n.member for n in Notification.objects.all()}, {self.coach})

    def test_a_person_in_two_clubs_counts_once_per_filter(self):
        ClubRole.objects.create(club=self.other_club, member=self.admin, role=ClubRole.Roles.ADMIN)

        self.assertEqual(audience_member_count(None, Announcement.Audience.ADMINS), 3)
        publish(Announcement.objects.create(title="Admins", message="Body", club=None, audience=Announcement.Audience.ADMINS))
        self.assertEqual(Notification.objects.count(), 3)
        self.assertEqual(Notification.objects.filter(member=self.admin).count(), 1)

    def test_the_audience_defaults_to_everyone(self):
        self.assertEqual(Announcement.objects.create(title="x", message="y").audience, Announcement.Audience.EVERYONE)


class SendTestTests(AnnouncementServiceTestBase):
    def test_emails_only_the_user_and_creates_nothing(self):
        make_account_member(self.club, "jane@example.com")
        admin = make_account_member(self.club, "admin@example.com", first_name="Admin")
        mail.outbox.clear()

        result = send_test(admin.user, title="Heads up", message="Body")

        self.assertTrue(result.email)
        self.assertEqual(result.pushed, 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["admin@example.com"])
        self.assertTrue(mail.outbox[0].subject.startswith("[TEST] "))
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(Announcement.objects.exists())

    def test_a_user_without_a_member_or_club_gets_only_the_email(self):
        mail.outbox.clear()

        result = send_test(self.superuser, title="Heads up", message="Body")

        self.assertTrue(result.email)
        self.assertIsNone(result.pushed)
        self.assertEqual([m.to for m in mail.outbox], [["root@example.com"]])


class ConsumeForAudienceTests(TestCase):
    """The one-time pop-up only shows a filtered announcement to people inside its audience."""

    def test_a_coaches_only_announcement_does_not_pop_up_for_a_player(self):
        from datetime import timedelta

        from django.contrib.auth import get_user_model

        from club.models import ClubMembership, Season
        from members.models import Member
        from rosterchief.test_support import make_club
        from teams.models import Team, TeamMembership

        club = make_club(name="Ajax United", slug="ajax-united")
        today = timezone.localdate()
        season = Season.objects.create(club=club, start_date=today - timedelta(days=30), end_date=today + timedelta(days=30))
        team = Team.objects.create(club=club, name="First")
        user = get_user_model().objects.create_user(email="player@example.com", password="x")
        member = Member.objects.create(user=user, first_name="Pla", last_name="Yer")
        ClubMembership.objects.create(club=club, member=member, season=season)
        TeamMembership.objects.create(team=team, member=member, season=season)
        Announcement.objects.create(club=club, title="Coaches", message="Only coaches", status=Announcement.Status.SENT, sent_at=timezone.now(), audience=Announcement.Audience.STAFF)

        self.assertEqual(consume_for(user, club), [])

        everyone = Announcement.objects.create(club=club, title="All", message="Everyone", status=Announcement.Status.SENT, sent_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(consume_for(user, club), [everyone])
