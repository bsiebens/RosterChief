"""Phase 3 (News, Notifications inbox + settings, Me, Edit profile, Security, Login/2FA) --
the iOS-parity behaviour added on top of mobile/tests.py."""

import datetime
import json

from allauth.mfa.models import Authenticator
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from club.models import ClubMembership, Season
from members.models import Family, FamilyMembership, Member
from news.models import News, NewsPhoto
from notifications.models import Notification
from rosterchief.test_support import make_club as _make_club
from teams.models import Position, Team, TeamMembership

HOST = "ajax-united.rosterchief.app"
ONE_PIXEL_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
User = get_user_model()
SETTINGS = {"ROSTERCHIEF_BASE_DOMAIN": "rosterchief.app", "ALLOWED_HOSTS": ["rosterchief.app", HOST, "testserver"]}


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = _make_club(secondary_color="#e4002b")
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="parent@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season)

    def setUp(self):
        self.client.force_login(self.user)

    def get(self, name, *, headers=None, **query):
        return self.client.get(reverse(name), query, HTTP_HOST=HOST, **(headers or {}))

    def add_child(self):
        family = Family.objects.create(club=self.club, name="Bakker")
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        child = Member.objects.create(first_name="Noor", last_name="Bakker")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=child, season=self.season)
        return child


@override_settings(**SETTINGS)
class NewsCardsTests(Base):
    def make_news(self, title, **kwargs):
        return News.objects.create(club=self.club, title=title, body=kwargs.pop("body", "Hello **world** of news."), status=News.Status.PUBLISHED, published_at=kwargs.pop("published_at", timezone.now()), **kwargs)

    def test_card_shows_meta_title_and_plain_text_excerpt(self):
        self.make_news("Big win", body="We **won** the cup. " * 3)

        response = self.get("mobile:news_list")

        self.assertContains(response, "Big win")
        self.assertContains(response, "We won the cup.")
        self.assertNotContains(response, "**won**")
        self.assertContains(response, "Club news")
        self.assertContains(response, "line-clamp-2")

    def test_card_without_a_picture_gets_a_placeholder_and_with_one_a_thumbnail(self):
        self.make_news("No picture")
        with_picture = self.make_news("With picture")
        NewsPhoto.objects.create(news_item=with_picture, image=SimpleUploadedFile("p.png", ONE_PIXEL_PNG, content_type="image/png"), is_main=True)

        response = self.get("mobile:news_list")

        self.assertContains(response, 'class="h-[72px] w-[72px] shrink-0 rounded-[10px] bg-line object-cover"', count=1)
        self.assertContains(response, "h-[72px] w-[72px] shrink-0 items-center justify-center rounded-[10px] bg-line text-dim", count=1)

    def test_load_more_sentinel_and_partial_page(self):
        for index in range(23):
            self.make_news(f"Post {index:02d}", published_at=timezone.now() - datetime.timedelta(minutes=index))

        first = self.get("mobile:news_list")
        self.assertContains(first, 'id="news-more"')
        self.assertContains(first, "?page=2")
        self.assertContains(first, "Post 00")
        self.assertNotContains(first, "Post 22")

        partial = self.get("mobile:news_list", headers={"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "news-more"}, page=2)
        self.assertContains(partial, "Post 22")
        self.assertNotContains(partial, "Post 00")
        self.assertNotContains(partial, "<html")
        self.assertNotContains(partial, 'id="news-more"')

    def test_empty_state(self):
        response = self.get("mobile:news_list")

        self.assertContains(response, "No news yet.")
        self.assertContains(response, "state-card")

    def test_external_only_and_other_club_news_stay_out(self):
        self.make_news("Public only", visibility=News.Visibility.EXTERNAL)
        other = _make_club(name="Other", slug="other", secondary_color="#e4002b")
        News.objects.create(club=other, title="Elsewhere", body="x", status=News.Status.PUBLISHED, published_at=timezone.now())

        response = self.get("mobile:news_list")

        self.assertNotContains(response, "Public only")
        self.assertNotContains(response, "Elsewhere")


@override_settings(**SETTINGS)
class InboxTests(Base):
    def notify(self, member=None, **kwargs):
        return Notification.objects.create(club=self.club, member=member or self.member, title=kwargs.pop("title", "Title"), body=kwargs.pop("body", "Body."), **kwargs)

    def post(self, action, htmx=True, **extra):
        headers = {"HTTP_HX_REQUEST": "true"} if htmx else {}
        return self.client.post(reverse("mobile:notifications"), {"action": action, **extra}, HTTP_HOST=HOST, **headers)

    def test_unread_row_is_marked_and_subtitle_is_relative_time(self):
        self.notify(title="Unread one")
        self.notify(title="Read one", read_at=timezone.now())

        response = self.get("mobile:notifications")

        self.assertContains(response, "Unread one")
        self.assertContains(response, "bg-red-600", count=2)  # the edge and the dot, unread row only
        self.assertContains(response, "ago")
        self.assertContains(response, "notif-menu")

    def test_person_name_only_when_the_account_manages_several_people(self):
        self.notify(title="Solo")
        self.assertNotContains(self.get("mobile:notifications"), "Lars &middot;")

        child = self.add_child()
        self.notify(member=child, title="For Noor")
        response = self.get("mobile:notifications")
        self.assertContains(response, "Noor &middot;")

    def test_text_follows_the_active_language(self):
        self.notify(title="English title", translations={"nl": {"title": "Nederlandse titel", "body": "Tekst."}})

        with override_settings(LANGUAGE_CODE="nl"):
            response = self.client.get(reverse("mobile:notifications"), HTTP_HOST=HOST, HTTP_ACCEPT_LANGUAGE="nl")

        self.assertContains(response, "Nederlandse titel")

    def test_mark_all_read_swaps_the_list_and_sends_a_toast_and_count(self):
        first = self.notify()
        self.notify()

        response = self.post("mark_all_read")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<html")
        self.assertContains(response, 'id="notification-list"')
        self.assertNotContains(response, "bg-red-600")
        trigger = json.loads(response["HX-Trigger"])
        self.assertTrue(trigger["rc:toast"])
        self.assertEqual(trigger["rc-unread"], 0)
        first.refresh_from_db()
        self.assertIsNotNone(first.read_at)

    def test_clear_all_deletes_and_shows_the_empty_state(self):
        self.notify()

        response = self.post("clear_all")

        self.assertContains(response, "state-card")
        self.assertFalse(Notification.objects.filter(club=self.club).exists())
        self.assertIn("rc:toast", json.loads(response["HX-Trigger"]))

    def test_plain_post_without_htmx_still_redirects(self):
        self.notify()

        response = self.post("mark_all_read", htmx=False)

        self.assertRedirects(response, reverse("mobile:notifications"), fetch_redirect_response=False)

    def test_tapping_a_row_without_a_destination_swaps_just_that_row(self):
        notification = self.notify(title="Nowhere to go")

        response = self.post("mark_read", notification_id=str(notification.pk))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nowhere to go")
        self.assertNotContains(response, "bg-red-600")
        self.assertEqual(json.loads(response["HX-Trigger"]), {"rc-unread": 0})
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)

    def test_cannot_mark_someone_elses_notification(self):
        stranger = Member.objects.create(first_name="Sam", last_name="Stranger")
        ClubMembership.objects.create(club=self.club, member=stranger, season=self.season)
        theirs = self.notify(member=stranger)

        response = self.post("mark_read", notification_id=str(theirs.pk))

        self.assertEqual(response.status_code, 400)

    def test_pages_load_more_through_the_sentinel(self):
        for index in range(25):
            self.notify(title=f"Item {index:02d}")

        first = self.get("mobile:notifications")
        self.assertContains(first, 'id="notif-more"')

        partial = self.get("mobile:notifications", headers={"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "notif-more"}, page=2)
        self.assertNotContains(partial, "<html")
        self.assertEqual(partial.content.decode().count('name="notification_id"'), 5)

    def test_other_clubs_notifications_do_not_show(self):
        other = _make_club(name="Other", slug="other", secondary_color="#e4002b")
        Notification.objects.create(club=other, member=self.member, title="Other club note", body="x")

        self.assertNotContains(self.get("mobile:notifications"), "Other club note")


@override_settings(**SETTINGS)
class SettingsAndMeTests(Base):
    def test_settings_screen_is_called_notifications_with_an_applies_to_picker(self):
        child = self.add_child()

        response = self.get("mobile:notification_settings")

        self.assertContains(response, "Applies to")
        self.assertContains(response, "Everyone")
        self.assertContains(response, f'<option value="{child.pk}"')
        self.assertContains(response, "Email preferences")

    def test_no_picker_without_children(self):
        self.assertNotContains(self.get("mobile:notification_settings"), "Applies to")

    def test_me_has_one_security_row_and_notifications_goes_to_settings(self):
        response = self.get("mobile:me")

        self.assertContains(response, f'href="{reverse("mobile:security")}"')
        self.assertContains(response, f'href="{reverse("mobile:notification_settings")}"')
        self.assertNotContains(response, "Notification emails")
        self.assertContains(response, "Sign out")

    def test_me_row_order_follows_ios(self):
        html = self.get("mobile:me").content.decode()

        positions = [html.index(label) for label in ("People I manage", "Forms", "Payments & dues", f'href="{reverse("mobile:notification_settings")}"', "Calendar sync", "Security", "Report a bug")]
        self.assertEqual(positions, sorted(positions))


@override_settings(**SETTINGS)
class SecurityTests(Base):
    def test_requires_login(self):
        self.client.logout()

        response = self.get("mobile:security")

        self.assertEqual(response.status_code, 302)

    def test_lists_only_my_passkeys_with_rename_and_remove(self):
        mine = Authenticator.objects.create(user=self.user, type=Authenticator.Type.WEBAUTHN, data={"name": "My iPhone", "credential": {}})
        other = User.objects.create_user(email="other@example.com", password="pw-secret-123")
        Authenticator.objects.create(user=other, type=Authenticator.Type.WEBAUTHN, data={"name": "Their laptop", "credential": {}})

        response = self.get("mobile:security")

        self.assertContains(response, "My iPhone")
        self.assertNotContains(response, "Their laptop")
        self.assertContains(response, reverse("mfa_edit_webauthn", kwargs={"pk": mine.pk}))
        self.assertContains(response, reverse("mfa_remove_webauthn", kwargs={"pk": mine.pk}))
        self.assertContains(response, reverse("mfa_add_webauthn"))
        self.assertContains(response, "never used")

    def test_empty_and_password_and_2fa_status(self):
        response = self.get("mobile:security")

        self.assertContains(response, "No passkeys yet.")
        self.assertContains(response, reverse("account_change_password"))
        self.assertContains(response, "No authenticator app is set up")

        Authenticator.objects.create(user=self.user, type=Authenticator.Type.TOTP, data={"secret": "x"})
        self.assertContains(self.get("mobile:security"), "An authenticator app is set up for this account.")


@override_settings(**SETTINGS)
class EditProfileExtrasTests(Base):
    def url(self, member):
        return reverse("mobile:edit_profile", kwargs={"member_id": member.pk})

    def test_teams_section_lists_current_season_rows(self):
        team = Team.objects.create(club=self.club, name="Ajax Under 12", short_name="U12")
        position = Position.objects.create(club=self.club, name="Forward")
        TeamMembership.objects.create(team=team, member=self.member, season=self.season, jersey_number=9, position=position)

        response = self.client.get(self.url(self.member), HTTP_HOST=HOST)

        self.assertContains(response, "Ajax Under 12")
        self.assertContains(response, "#9")
        self.assertContains(response, "Forward")

    def test_no_teams_section_without_teams(self):
        response = self.client.get(self.url(self.member), HTTP_HOST=HOST)

        self.assertNotContains(response, ">Teams<")
        self.assertNotContains(response, "Remove photo")

    def test_saved_state_after_saving(self):
        response = self.client.post(self.url(self.member), {"first_name": "Lars", "last_name": "Bakker", "email": "parent@example.com", "phone": "", "emergency_phone": ""}, HTTP_HOST=HOST, follow=True)

        self.assertContains(response, "Saved")
        self.assertEqual(response.redirect_chain[-1][0], f"{self.url(self.member)}?saved=1")

    def test_remove_photo(self):
        self.member.profile_photo.save("me.png", SimpleUploadedFile("me.png", ONE_PIXEL_PNG, content_type="image/png"))
        self.assertContains(self.client.get(self.url(self.member), HTTP_HOST=HOST), "Remove photo")

        response = self.client.post(self.url(self.member), {"action": "remove_photo"}, HTTP_HOST=HOST)

        self.assertRedirects(response, self.url(self.member), fetch_redirect_response=False)
        self.member.refresh_from_db()
        self.assertFalse(self.member.profile_photo)

    def test_cannot_remove_an_unmanaged_members_photo(self):
        stranger = Member.objects.create(first_name="Sam", last_name="Stranger")

        response = self.client.post(self.url(stranger), {"action": "remove_photo"}, HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 404)


@override_settings(**SETTINGS)
class BugStatusLabelTests(Base):
    def test_status_labels(self):
        from bugs.models import BugReport

        labels = [label for _value, label in BugReport.Status.choices]

        self.assertEqual(labels, ["Submitted", "In progress", "Fixed", "Won't fix"])


@override_settings(**SETTINGS)
class LoginSkinTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = _make_club(secondary_color="#e4002b")

    def mobile_login(self):
        self.client.get(reverse("mobile:home"), HTTP_HOST=HOST)  # sets the mobile_context session flag
        return self.client.get(reverse("account_login"), HTTP_HOST=HOST)

    def test_app_login_has_tagline_full_width_button_and_dark_mode_meta(self):
        response = self.mobile_login()

        self.assertTemplateUsed(response, "mobile/_auth_base.html")
        self.assertContains(response, "Sign in to see your family's games.")
        self.assertContains(response, 'class="btn-rc btn-rc-primary"')
        self.assertContains(response, 'name="color-scheme" content="light dark"')

    def test_public_login_keeps_the_original_layout(self):
        response = self.client.get(reverse("account_login"), HTTP_HOST=HOST)

        self.assertNotContains(response, "btn-rc")
        self.assertContains(response, "Sign In")
