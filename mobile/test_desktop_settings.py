"""Desktop counterparts of the Me sub-screens (notification settings, live game updates, security, club picker):
with a desktop User-Agent each serves mobile/desktop/* instead of the phone shell."""

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from waffle import get_waffle_flag_model

from club.models import ClubMembership
from notifications.models import NotificationCategory
from rosterchief.test_support import make_club

from .test_desktop_screens import DESKTOP_SHELL, DESKTOP_UA
from .test_member_screens import HOST, MemberScreenFixture, setUpModule, tearDownModule  # noqa: F401 (module fixtures)

CSS = "mobile-desktop.css"


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", HOST, "testserver"])
class DesktopSettingsScreenTests(MemberScreenFixture, TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client.force_login(self.user)

    def get_desktop(self, name):
        return self.get(reverse(name), HTTP_USER_AGENT=DESKTOP_UA)

    def assert_desktop(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, CSS)
        self.assertContains(response, DESKTOP_SHELL)
        self.assertContains(response, reverse("mobile:me"))  # the back link

    def test_notification_settings_desktop(self):
        self.add_child()
        response = self.get_desktop("mobile:notification_settings")

        self.assert_desktop(response)
        self.assertContains(response, 'name="master__push"')
        self.assertContains(response, 'x-data="{ push: true, email: true }"')
        self.assertContains(response, 'name="person"')  # the Applies to picker
        for value in NotificationCategory.values:
            self.assertContains(response, f'name="push__{value}"')
            self.assertContains(response, f'name="email__{value}"')
        self.assertContains(response, 'name="quiet_hours"')
        self.assertContains(response, 'name="email_notifications"')

    def test_notification_settings_post_from_desktop_page_saves(self):
        url = reverse("mobile:notification_settings")
        data = {"category_switches": "1", "master_switches": "1", "master__push": "on"}
        response = self.client.post(url, data, HTTP_HOST=HOST, HTTP_USER_AGENT=DESKTOP_UA)

        self.assertRedirects(response, url, fetch_redirect_response=False)
        page = self.get(url, HTTP_USER_AGENT=DESKTOP_UA)
        self.assertContains(page, "Customised")
        self.assertContains(page, 'x-data="{ push: true, email: false }"')

    def test_live_game_updates_desktop(self):
        self.assertEqual(self.get_desktop("mobile:live_game_updates").status_code, 404)
        get_waffle_flag_model().objects.get_or_create(name="game_timeline")[0].clubs.add(self.club)
        cache.clear()

        response = self.get_desktop("mobile:live_game_updates")

        self.assert_desktop(response)
        self.assertContains(response, f'name="team" value="{self.team.pk}"')
        url = reverse("mobile:live_game_updates")
        post = self.client.post(url, {"team": [str(self.team.pk)]}, HTTP_HOST=HOST, HTTP_USER_AGENT=DESKTOP_UA)
        self.assertRedirects(post, url, fetch_redirect_response=False)
        self.assertContains(self.get(url, HTTP_USER_AGENT=DESKTOP_UA), "checked")

    def test_security_desktop(self):
        response = self.get_desktop("mobile:security")

        self.assert_desktop(response)
        self.assertContains(response, reverse("account_change_password"))
        self.assertContains(response, reverse("mfa_index"))

    def test_club_picker_desktop(self):
        other = make_club(name="Bravo FC", slug="bravo-fc")
        ClubMembership.objects.create(club=other, member=self.member, season=self.season.__class__.objects.create(club=other, start_date=self.season.start_date, end_date=self.season.end_date))

        response = self.get_desktop("mobile:club_picker")

        self.assert_desktop(response)
        self.assertContains(response, "Choose Your Club")
        self.assertContains(response, "Bravo FC")
        self.assertContains(response, "http://bravo-fc.rosterchief.app/app/")

    def test_phone_still_gets_the_phone_template(self):
        response = self.get(reverse("mobile:security"), HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148")
        self.assertNotContains(response, CSS)
