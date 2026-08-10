import datetime

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from club.models import Club

from .models import News, NewsPhoto


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
