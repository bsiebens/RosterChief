"""Preference precedence, master switches, email mode and quiet hours."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from club.models import Club

from . import preferences
from .models import PushDevice
from .tests_support import make_device, make_user


class PreferenceTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def clubs(self):
        # Club rows only need a primary key here; a tenant is overkill for preference precedence, so reuse the model without creating a schema.
        a = Club(name="A", schema_name="pref_a", owner=self.user)
        b = Club(name="B", schema_name="pref_b", owner=self.user)
        for club in (a, b):
            club.auto_create_schema = False
            club.save()
        return a, b

    def test_default_is_on(self):
        self.assertTrue(preferences.is_enabled(self.user, "news", "push"))
        self.assertTrue(preferences.is_enabled(self.user, "news", "email"))

    def test_all_clubs_row_applies_everywhere(self):
        a, _b = self.clubs()
        preferences.set_preference(self.user, "news", "push", False)
        self.assertFalse(preferences.is_enabled(self.user, "news", "push"))
        self.assertFalse(preferences.is_enabled(self.user, "news", "push", a))
        self.assertTrue(preferences.is_enabled(self.user, "event", "push", a))
        self.assertTrue(preferences.is_enabled(self.user, "news", "email", a))

    def test_club_row_beats_all_clubs_row_both_ways(self):
        a, b = self.clubs()
        preferences.set_preference(self.user, "news", "push", False)
        preferences.set_preference(self.user, "news", "push", True, club=a)
        self.assertTrue(preferences.is_enabled(self.user, "news", "push", a))
        self.assertFalse(preferences.is_enabled(self.user, "news", "push", b))
        preferences.set_preference(self.user, "event", "push", True)
        preferences.set_preference(self.user, "event", "push", False, club=b)
        self.assertTrue(preferences.is_enabled(self.user, "event", "push", a))
        self.assertFalse(preferences.is_enabled(self.user, "event", "push", b))

    def test_set_preference_updates_in_place_and_validates(self):
        preferences.set_preference(self.user, "news", "push", False)
        preferences.set_preference(self.user, "news", "push", True)
        self.assertEqual(self.user.notification_preferences.count(), 1)
        with self.assertRaises(ValueError):
            preferences.set_preference(self.user, "nope", "push", True)
        with self.assertRaises(ValueError):
            preferences.is_enabled(self.user, "news", "sms")

    def test_master_switch_sits_on_top(self):
        a, b = self.clubs()
        preferences.set_preference(self.user, "news", "push", True, club=a)
        preferences.set_master_switch(self.user, "push", False)
        self.assertFalse(preferences.is_enabled(self.user, "news", "push", a))
        self.assertTrue(preferences.is_enabled(self.user, "news", "email", a))  # the other channel is untouched
        preferences.set_master_switch(self.user, "push", True, club=a)  # club master beats the all-clubs master
        self.assertTrue(preferences.is_enabled(self.user, "news", "push", a))
        self.assertFalse(preferences.is_enabled(self.user, "news", "push", b))

    def test_email_mode(self):
        self.assertEqual(preferences.get_email_mode(self.user), "always")
        preferences.set_email_mode(self.user, "unless_app")
        self.assertEqual(preferences.get_email_mode(self.user), "unless_app")
        with self.assertRaises(ValueError):
            preferences.set_email_mode(self.user, "never")

    def test_unless_app_skips_email_only_with_a_fresh_enabled_device(self):
        preferences.set_email_mode(self.user, "unless_app")
        self.assertTrue(preferences.should_email(self.user, "news"))  # no device
        device = make_device(self.user)
        self.assertFalse(preferences.should_email(self.user, "news"))
        PushDevice.objects.filter(pk=device.pk).update(enabled=False)
        self.assertTrue(preferences.should_email(self.user, "news"))
        PushDevice.objects.filter(pk=device.pk).update(enabled=True, last_seen=timezone.now() - timedelta(days=61))
        self.assertTrue(preferences.should_email(self.user, "news"))  # stale (> PushSettings.stale_device_days)

    def test_always_mode_ignores_devices_but_respects_the_email_switches(self):
        make_device(self.user)
        self.assertTrue(preferences.should_email(self.user, "news"))
        preferences.set_preference(self.user, "news", "email", False)
        self.assertFalse(preferences.should_email(self.user, "news"))


class QuietHoursTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def at(self, hour, minute=0, day=2, tz="Europe/Brussels"):
        return datetime(2026, 11, day, hour, minute, tzinfo=ZoneInfo(tz)).astimezone(UTC)

    def test_none_configured_or_disabled(self):
        self.assertEqual(preferences.quiet_state(self.user), (False, None))
        preferences.set_quiet_hours(self.user, time(22), time(7), enabled=False)
        self.assertEqual(preferences.quiet_state(self.user, self.at(23)), (False, None))

    def test_same_day_window(self):
        preferences.set_quiet_hours(self.user, time(13), time(15))
        self.assertEqual(preferences.quiet_state(self.user, self.at(12, 59)), (False, None))
        self.assertEqual(preferences.quiet_state(self.user, self.at(13)), (True, self.at(15)))
        self.assertEqual(preferences.quiet_state(self.user, self.at(14, 59))[0], True)
        self.assertEqual(preferences.quiet_state(self.user, self.at(15)), (False, None))

    def test_window_across_midnight(self):
        preferences.set_quiet_hours(self.user, time(22), time(7))
        self.assertEqual(preferences.quiet_state(self.user, self.at(21, 59)), (False, None))
        self.assertEqual(preferences.quiet_state(self.user, self.at(23, 30)), (True, self.at(7, day=3)))  # ends tomorrow morning
        self.assertEqual(preferences.quiet_state(self.user, self.at(2)), (True, self.at(7)))  # ends this morning
        self.assertEqual(preferences.quiet_state(self.user, self.at(7)), (False, None))

    def test_it_is_evaluated_in_its_own_time_zone(self):
        preferences.set_quiet_hours(self.user, time(22), time(7), "America/New_York")
        # 23:30 in Brussels is 17:30 in New York: not quiet there yet.
        self.assertEqual(preferences.quiet_state(self.user, self.at(23, 30)), (False, None))
        self.assertTrue(preferences.quiet_state(self.user, self.at(23, 30, tz="America/New_York"))[0])

    def test_validation_and_clear(self):
        with self.assertRaises(ValueError):
            preferences.set_quiet_hours(self.user, time(7), time(7))
        with self.assertRaises(ValueError):
            preferences.set_quiet_hours(self.user, time(7), time(8), "Mars/Base")
        preferences.set_quiet_hours(self.user, time(7), time(8))
        self.assertTrue(preferences.clear_quiet_hours(self.user))
        self.assertFalse(preferences.clear_quiet_hours(self.user))
