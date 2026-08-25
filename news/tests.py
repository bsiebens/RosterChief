import datetime

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from club.models import Club, ClubMembership, ClubRole, Season
from features.models import JobRun, JobToggle, Maintenance
from members.models import Family, FamilyMembership, Member
from notifications.models import Notification
from teams.models import Position, Team, TeamMembership

from .models import News, NewsPhoto
from .services import notify_editors_of_pending_review, send_publish_notification

User = get_user_model()


def make_season(club, start_year=2026):
    return Season.objects.create(club=club, start_date=datetime.date(start_year, 8, 1), end_date=datetime.date(start_year + 1, 5, 31))


def make_photo(news_item, *, is_main=False):
    image = SimpleUploadedFile("photo.jpg", b"fake-image-bytes", content_type="image/jpeg")
    return NewsPhoto.objects.create(news_item=news_item, image=image, is_main=is_main)


class NewsModelTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_slug_is_derived_from_title(self):
        item = News.objects.create(club=self.club, title="Season Kickoff", body="Body text.")

        self.assertEqual(item.slug, "season-kickoff")

    def test_slug_is_unique_per_club_not_globally(self):
        News.objects.create(club=self.club, title="Season Kickoff", body="First.")
        second = News.objects.create(club=self.club, title="Season Kickoff", body="Second.")

        self.assertEqual(second.slug, "season-kickoff-2")

    def test_two_clubs_can_share_the_same_slug(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        News.objects.create(club=self.club, title="Season Kickoff", body="First.")

        other = News.objects.create(club=other_club, title="Season Kickoff", body="Other club.")

        self.assertEqual(other.slug, "season-kickoff")

    def test_defaults_to_draft_and_internal(self):
        item = News.objects.create(club=self.club, title="Draft item", body="Body.")

        self.assertEqual(item.status, News.Status.DRAFT)
        self.assertEqual(item.visibility, News.Visibility.INTERNAL)
        self.assertIsNone(item.published_at)

    def test_publish_defaults_the_publish_date_to_now(self):
        item = News.objects.create(club=self.club, title="Item", body="Body.")

        item.publish()

        self.assertEqual(item.status, News.Status.PUBLISHED)
        self.assertIsNotNone(item.published_at)
        self.assertFalse(item.is_scheduled)

    def test_publish_accepts_a_future_date_and_is_scheduled(self):
        item = News.objects.create(club=self.club, title="Item", body="Body.")
        future = timezone.now() + datetime.timedelta(days=7)

        item.publish(at=future)

        self.assertEqual(item.status, News.Status.PUBLISHED)
        self.assertEqual(item.published_at, future)
        self.assertTrue(item.is_scheduled)

    def test_submit_for_review_moves_a_draft_to_pending_review(self):
        item = News.objects.create(club=self.club, title="Item", body="Body.")

        item.submit_for_review()

        self.assertEqual(item.status, News.Status.PENDING_REVIEW)

    def test_unpublish_clears_the_publish_date(self):
        item = News.objects.create(club=self.club, title="Item", body="Body.")
        item.publish()

        item.unpublish()

        self.assertEqual(item.status, News.Status.DRAFT)
        self.assertIsNone(item.published_at)

    def test_effective_english_falls_back_to_the_original_when_blank(self):
        item = News.objects.create(club=self.club, title="Seizoensstart", body="We beginnen het seizoen.")

        self.assertEqual(item.effective_title_en, "Seizoensstart")
        self.assertEqual(item.effective_body_en, "We beginnen het seizoen.")

    def test_effective_english_uses_its_own_text_when_set(self):
        item = News.objects.create(club=self.club, title="Seizoensstart", body="We beginnen het seizoen.", title_en="Season kickoff", body_en="We're starting the season.")

        self.assertEqual(item.effective_title_en, "Season kickoff")
        self.assertEqual(item.effective_body_en, "We're starting the season.")

    def test_effective_english_stays_current_after_the_original_changes(self):
        # Read-time fallback, not copy-on-save: editing the Dutch text later must
        # not leave a stale English "copy" behind.
        item = News.objects.create(club=self.club, title="Seizoensstart", body="We beginnen het seizoen.")

        item.title, item.body = "Nieuwe titel", "Nieuwe tekst."
        item.save(update_fields=["title", "body"])

        self.assertEqual(item.effective_title_en, "Nieuwe titel")
        self.assertEqual(item.effective_body_en, "Nieuwe tekst.")


class NewsPhotoModelTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.item = News.objects.create(club=self.club, title="Match report", body="Body.")

    def test_a_second_main_photo_is_rejected_at_the_database_level(self):
        make_photo(self.item, is_main=True)

        with self.assertRaises(IntegrityError):
            make_photo(self.item, is_main=True)

    def test_two_non_main_photos_are_fine(self):
        make_photo(self.item, is_main=False)
        make_photo(self.item, is_main=False)

        self.assertEqual(self.item.photos.count(), 2)

    def test_two_different_news_items_can_each_have_a_main_photo(self):
        other_item = News.objects.create(club=self.club, title="Other item", body="Body.")

        make_photo(self.item, is_main=True)
        make_photo(other_item, is_main=True)

        self.assertEqual(NewsPhoto.objects.filter(is_main=True).count(), 2)

    def test_object_position_defaults_to_dead_centre(self):
        photo = make_photo(self.item)

        self.assertEqual(photo.object_position, "50% 50%")

    def test_object_position_reflects_a_custom_focal_point(self):
        photo = make_photo(self.item)
        photo.focal_x = 20
        photo.focal_y = 80
        photo.save(update_fields=["focal_x", "focal_y"])

        self.assertEqual(photo.object_position, "20% 80%")


class SendPublishNotificationTests(TestCase):
    """news.services.send_publish_notification -- the audience is this item's
    teams' current rosters, or every active member if it's club-wide. Called
    directly here (not through the sweep command) to test audience
    resolution in isolation; NotifyPublishedNewsCommandTests below covers
    the sweep's own due/already-notified/toggle behavior."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.season = make_season(cls.club)
        cls.position = Position.objects.create(club=cls.club, name="Player", short_name="P")

    def make_member(self, first_name, *, status=ClubMembership.StatusChoices.ACTIVE, kind=ClubMembership.Kind.MEMBER, email=None):
        member = Member.objects.create(first_name=first_name, last_name="Member", email=email or f"{first_name.lower()}@example.com")
        if email:
            User.objects.create_user(email=email, password="pw-secret-123")
            member.user = User.objects.get(email=email)
            member.save(update_fields=["user"])
        ClubMembership.objects.create(club=self.club, member=member, season=self.season, status=status, kind=kind)
        return member

    def test_club_wide_news_notifies_every_active_member(self):
        member = self.make_member("Jamie", email="jamie@example.com")
        news_item = News.objects.create(club=self.club, title="Big news", body="Something happened.", status=News.Status.PUBLISHED, published_at=timezone.now())

        notifications = send_publish_notification(news_item)

        self.assertTrue(Notification.objects.filter(club=self.club, member=member, title="Big news").exists())
        self.assertEqual(len(notifications), 1)

    def test_team_scoped_news_only_notifies_that_teams_roster(self):
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        other_team = Team.objects.create(club=self.club, name="U18", short_name="U18")
        on_team = self.make_member("Jamie", email="jamie@example.com")
        off_team = self.make_member("Alex", email="alex@example.com")
        TeamMembership.objects.create(team=team, member=on_team, season=self.season, position=self.position)
        TeamMembership.objects.create(team=other_team, member=off_team, season=self.season, position=self.position)
        news_item = News.objects.create(club=self.club, title="Team news", body="Training moved.", status=News.Status.PUBLISHED, published_at=timezone.now())
        news_item.teams.add(team)

        send_publish_notification(news_item)

        self.assertTrue(Notification.objects.filter(member=on_team).exists())
        self.assertFalse(Notification.objects.filter(member=off_team).exists())

    def test_excludes_an_inactive_member(self):
        self.make_member("Jamie", status=ClubMembership.StatusChoices.PENDING, email="jamie@example.com")
        news_item = News.objects.create(club=self.club, title="News", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now())

        send_publish_notification(news_item)

        self.assertFalse(Notification.objects.exists())

    def test_excludes_a_guardian(self):
        self.make_member("Alex", kind=ClubMembership.Kind.GUARDIAN, email="alex@example.com")
        news_item = News.objects.create(club=self.club, title="News", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now())

        send_publish_notification(news_item)

        self.assertFalse(Notification.objects.exists())

    def test_siblings_sharing_a_guardian_are_notified_once(self):
        # Two children, no login of their own, both reachable only through the
        # same parent -- one Notification/one email for the family, not two.
        parent_user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        parent = Member.objects.create(first_name="Pat", last_name="Parent", email="parent@example.com", user=parent_user)
        family = Family.objects.create(name="Parent family")
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)
        child_a = Member.objects.create(first_name="Ana", last_name="Parent")
        child_b = Member.objects.create(first_name="Ben", last_name="Parent")
        for child in (child_a, child_b):
            FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
            ClubMembership.objects.create(club=self.club, member=child, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        news_item = News.objects.create(club=self.club, title="Club news", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now())

        notifications = send_publish_notification(news_item)

        self.assertEqual(Notification.objects.filter(club=self.club, title="Club news").count(), 1)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["parent@example.com"])

    def test_children_with_different_guardians_are_each_notified(self):
        family_one = Family.objects.create(name="First family")
        family_two = Family.objects.create(name="Second family")
        for family_name, family in (("one", family_one), ("two", family_two)):
            parent_user = User.objects.create_user(email=f"parent-{family_name}@example.com", password="pw-secret-123")
            parent = Member.objects.create(first_name=f"Parent{family_name}", last_name="Adult", email=f"parent-{family_name}@example.com", user=parent_user)
            FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)
            child = Member.objects.create(first_name=f"Child{family_name}", last_name="Kid")
            FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
            ClubMembership.objects.create(club=self.club, member=child, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        news_item = News.objects.create(club=self.club, title="Club news", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now())

        notifications = send_publish_notification(news_item)

        self.assertEqual(Notification.objects.filter(club=self.club, title="Club news").count(), 2)
        self.assertEqual(len(notifications), 2)
        self.assertEqual(len(mail.outbox), 2)

    def test_the_body_is_plain_text_not_markdown(self):
        member = self.make_member("Jamie", email="jamie@example.com")
        news_item = News.objects.create(club=self.club, title="News", body="**Bold** text.", status=News.Status.PUBLISHED, published_at=timezone.now())

        send_publish_notification(news_item)

        notification = Notification.objects.get(member=member)
        self.assertEqual(notification.body, "Bold text.")
        self.assertEqual(len(mail.outbox), 1)


class NotifyPublishedNewsCommandTests(TestCase):
    """The notify_published_news management command -- the periodic sweep that
    replaced the old Celery ETA-scheduled dispatch. See that command's own
    docstring for the News.notified_at idempotency design."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.season = make_season(cls.club)
        cls.member = Member.objects.create(first_name="Jamie", last_name="Member", email="jamie@example.com")
        User.objects.create_user(email="jamie@example.com", password="pw-secret-123")
        cls.member.user = User.objects.get(email="jamie@example.com")
        cls.member.save(update_fields=["user"])
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season, status=ClubMembership.StatusChoices.ACTIVE)

    def setUp(self):
        # JobToggle/Maintenance are cache-backed (see their own models -- a flip has to
        # reach every process, not just the one that made it), so a toggle left disabled
        # by one test would otherwise leak into the next via the shared LocMemCache.
        cache.clear()
        self.addCleanup(cache.clear)

    def test_notifies_a_published_item_past_its_publish_time(self):
        news_item = News.objects.create(club=self.club, title="News", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now() - datetime.timedelta(minutes=1))

        result = call_command("notify_published_news")

        self.assertTrue(Notification.objects.filter(member=self.member, title="News").exists())
        news_item.refresh_from_db()
        self.assertIsNotNone(news_item.notified_at)
        self.assertIn("Notified 1 member(s) across 1 news item(s)", result)

    def test_skips_a_news_item_already_notified(self):
        already = timezone.now() - datetime.timedelta(hours=1)
        News.objects.create(club=self.club, title="News", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now() - datetime.timedelta(minutes=1), notified_at=already)

        call_command("notify_published_news")

        self.assertFalse(Notification.objects.exists())

    def test_skips_a_news_item_not_yet_due(self):
        News.objects.create(club=self.club, title="News", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now() + datetime.timedelta(hours=1))

        call_command("notify_published_news")

        self.assertFalse(Notification.objects.exists())

    def test_skips_a_news_item_that_is_not_published(self):
        News.objects.create(club=self.club, title="News", body="Body.", status=News.Status.DRAFT)

        call_command("notify_published_news")

        self.assertFalse(Notification.objects.exists())

    def test_skips_a_news_item_opted_out_of_notification(self):
        # notified_at set immediately at publish time (management.views.NewsPublishView.
        # form_valid, when notify_members is unchecked) -- opted out entirely, not just
        # delayed, so the sweep must never send one regardless of why notified_at is set.
        News.objects.create(club=self.club, title="News", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now() - datetime.timedelta(minutes=1), notified_at=timezone.now())

        call_command("notify_published_news")

        self.assertFalse(Notification.objects.exists())

    def test_a_quiet_sweep_is_not_an_error(self):
        result = call_command("notify_published_news")

        self.assertIn("Notified 0 member(s) across 0 news item(s)", result)

    def test_writes_a_job_run_row(self):
        News.objects.create(club=self.club, title="News", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now() - datetime.timedelta(minutes=1))

        call_command("notify_published_news")

        job_run = JobRun.objects.get(name="news.tasks.notify_news_published")
        self.assertEqual(job_run.status, JobRun.Status.SUCCESS)

    def test_stands_down_when_disabled_via_job_toggle(self):
        JobToggle.set_enabled("news.tasks.notify_news_published", False)
        News.objects.create(club=self.club, title="News", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now() - datetime.timedelta(minutes=1))

        with self.assertRaises(CommandError):
            call_command("notify_published_news")

        self.assertFalse(Notification.objects.exists())
        job_run = JobRun.objects.get(name="news.tasks.notify_news_published")
        self.assertEqual(job_run.status, JobRun.Status.FAILURE)

    def test_stands_down_during_maintenance(self):
        Maintenance.start(message="Upgrading.")
        News.objects.create(club=self.club, title="News", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now() - datetime.timedelta(minutes=1))

        with self.assertRaises(CommandError):
            call_command("notify_published_news")

        self.assertFalse(Notification.objects.exists())


class NotifyEditorsOfPendingReviewTests(TestCase):
    """news.services.notify_editors_of_pending_review -- in-app only (no
    email), every ADMIN/EDITOR for the club."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def make_role(self, first_name, role):
        member = Member.objects.create(first_name=first_name, last_name="Staff")
        ClubRole.objects.create(club=self.club, member=member, role=role)
        return member

    def test_notifies_admins_and_editors(self):
        admin = self.make_role("Ada", ClubRole.Roles.ADMIN)
        editor = self.make_role("Ed", ClubRole.Roles.EDITOR)
        author = self.make_role("Cara", ClubRole.Roles.MEMBER)
        news_item = News.objects.create(club=self.club, title="Draft item", body="Body.", created_by=author)

        notify_editors_of_pending_review(news_item)

        self.assertTrue(Notification.objects.filter(member=admin).exists())
        self.assertTrue(Notification.objects.filter(member=editor).exists())
        self.assertFalse(Notification.objects.filter(member=author).exists())

    def test_sends_no_email(self):
        self.make_role("Ada", ClubRole.Roles.ADMIN)
        news_item = News.objects.create(club=self.club, title="Draft item", body="Body.")

        notify_editors_of_pending_review(news_item)

        self.assertEqual(len(mail.outbox), 0)
        self.assertIsNone(Notification.objects.first().sent_at)

    def test_the_notification_names_the_author(self):
        self.make_role("Ada", ClubRole.Roles.ADMIN)
        author = Member.objects.create(first_name="Cara", last_name="Coach")
        news_item = News.objects.create(club=self.club, title="Draft item", body="Body.", created_by=author)

        notify_editors_of_pending_review(news_item)

        self.assertIn("Cara Coach", Notification.objects.first().body)
