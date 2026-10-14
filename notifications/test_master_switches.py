"""The "All push" / "All emails" master switches: off silences the channel whatever the categories say."""

from django.core import mail
from django.test import TestCase, override_settings

from . import preferences
from . import test_preferences_enforcement as enforcement
from .models import NotificationMasterSwitch


class MasterSwitchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from authentication.models import User
        from club.models import Club

        cls.club = Club.objects.create(name="Mast FC", slug="mastfc")
        cls.other = Club.objects.create(name="Other FC", slug="otherfc")
        cls.user = User.objects.create_user(email="master@example.com", password="x")

    def test_default_on(self):
        self.assertTrue(preferences.is_enabled(self.user, self.club, "news", "push"))
        self.assertEqual(preferences.master_matrix(self.user, self.club), {"push": True, "email": True})

    def test_master_off_beats_category_on(self):
        preferences.set_preference(self.user, self.club, "news", "push", True)
        preferences.set_master(self.user, self.club, "push", False)
        self.assertFalse(preferences.is_enabled(self.user, self.club, "news", "push"))
        self.assertTrue(preferences.is_enabled(self.user, self.club, "news", "email"))
        self.assertTrue(preferences.is_enabled(self.user, self.other, "news", "push"))
        self.assertIn(self.user.pk, preferences.users_with_disabled([self.user.pk], self.club, "news", "push"))

    def test_global_master_and_specificity(self):
        preferences.set_master(self.user, None, "email", False)
        self.assertFalse(preferences.is_enabled(self.user, self.club, "news", "email"))
        preferences.set_master(self.user, self.club, "email", True)  # a club-level "on" is more specific
        self.assertTrue(preferences.is_enabled(self.user, self.club, "news", "email"))
        self.assertFalse(preferences.is_enabled(self.user, self.other, "news", "email"))

    def test_by_member_variant_honours_master(self):
        preferences.set_master(self.user, self.club, "push", False)
        result = preferences.users_with_disabled_by_member([self.user.pk], self.club, "news", "push", [None, 12345])
        self.assertEqual(result[12345], {self.user.pk})

    def test_reset_all_drops_masters_but_category_reset_does_not(self):
        preferences.set_master(self.user, self.club, "push", False)
        preferences.reset(self.user, self.club, "news")
        self.assertEqual(NotificationMasterSwitch.objects.count(), 1)
        preferences.reset(self.user, self.club)
        self.assertEqual(NotificationMasterSwitch.objects.count(), 0)


@override_settings(APP_PUSH_GATEWAY="notifications.test_preferences_enforcement.RecordingGateway", VAPID_PRIVATE_KEY="test-key", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MasterEndToEndTests(enforcement.PreferenceEnforcementTests):
    """A master-off account gets nothing on that channel for a category that is individually on."""

    def test_master_push_off_skips_native_and_web_but_keeps_email_and_inbox(self):
        self.mute(self.user, "push", enabled=True)  # the category itself is explicitly on
        preferences.set_master(self.user, None, "push", False)
        preferences.set_master(self.web_user, self.club, "push", False)

        notifications, webpush = self.run_dispatch(self.member, self.web_member)

        self.assertEqual(enforcement.RecordingGateway.published, [])
        webpush.assert_not_called()
        self.assertEqual(len(notifications), 2)
        self.assertEqual(len(mail.outbox), 2)

    def test_master_email_off_skips_mail_but_keeps_push(self):
        self.mute(self.user, "email", enabled=True)
        preferences.set_master(self.user, self.club, "email", False)

        self.run_dispatch(self.member)

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(enforcement.RecordingGateway.published, ["parent-phone"])

    def test_master_back_on_lets_the_categories_decide(self):
        preferences.set_master(self.user, self.club, "push", False)
        preferences.set_master(self.user, self.club, "push", True)
        self.mute(self.user, "push")  # category off

        self.run_dispatch(self.member)

        self.assertEqual(enforcement.RecordingGateway.published, [])
