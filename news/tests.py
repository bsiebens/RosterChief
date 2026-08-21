import datetime

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from club.models import Club, ClubMembership, Season
from members.models import Member
from notifications.models import Notification
from teams.models import Position, Team, TeamMembership

from .models import News, NewsPhoto
from .tasks import notify_news_published

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


class NotifyNewsPublishedTests(TestCase):
    """news.tasks.notify_news_published -- the audience is this item's teams'
    current rosters, or every active member if it's club-wide."""

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

        result = notify_news_published(news_item.pk)

        self.assertTrue(Notification.objects.filter(club=self.club, member=member, title="Big news").exists())
        self.assertIn("Notified 1", result)

    def test_team_scoped_news_only_notifies_that_teams_roster(self):
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        other_team = Team.objects.create(club=self.club, name="U18", short_name="U18")
        on_team = self.make_member("Jamie", email="jamie@example.com")
        off_team = self.make_member("Alex", email="alex@example.com")
        TeamMembership.objects.create(team=team, member=on_team, season=self.season, position=self.position)
        TeamMembership.objects.create(team=other_team, member=off_team, season=self.season, position=self.position)
        news_item = News.objects.create(club=self.club, title="Team news", body="Training moved.", status=News.Status.PUBLISHED, published_at=timezone.now())
        news_item.teams.add(team)

        notify_news_published(news_item.pk)

        self.assertTrue(Notification.objects.filter(member=on_team).exists())
        self.assertFalse(Notification.objects.filter(member=off_team).exists())

    def test_excludes_an_inactive_member(self):
        self.make_member("Jamie", status=ClubMembership.StatusChoices.PENDING, email="jamie@example.com")
        news_item = News.objects.create(club=self.club, title="News", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now())

        notify_news_published(news_item.pk)

        self.assertFalse(Notification.objects.exists())

    def test_excludes_a_guardian(self):
        self.make_member("Alex", kind=ClubMembership.Kind.GUARDIAN, email="alex@example.com")
        news_item = News.objects.create(club=self.club, title="News", body="Body.", status=News.Status.PUBLISHED, published_at=timezone.now())

        notify_news_published(news_item.pk)

        self.assertFalse(Notification.objects.exists())

    def test_skips_a_news_item_that_is_no_longer_published(self):
        news_item = News.objects.create(club=self.club, title="News", body="Body.", status=News.Status.DRAFT)

        result = notify_news_published(news_item.pk)

        self.assertEqual(result, "Skipped: not published.")
        self.assertFalse(Notification.objects.exists())

    def test_the_body_is_plain_text_not_markdown(self):
        member = self.make_member("Jamie", email="jamie@example.com")
        news_item = News.objects.create(club=self.club, title="News", body="**Bold** text.", status=News.Status.PUBLISHED, published_at=timezone.now())

        notify_news_published(news_item.pk)

        notification = Notification.objects.get(member=member)
        self.assertEqual(notification.body, "Bold text.")
        self.assertEqual(len(mail.outbox), 1)
