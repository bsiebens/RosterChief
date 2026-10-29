"""Notification preferences (notifications.preferences) enforced by every channel: push is one switch for
native and web together, email is its own, the inbox row is never affected, and an invoice mail is a document
that no preference suppresses."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings

from api.models import PushDevice
from api.protected.push import PushGateway
from api.protected.tests import AppApiTestCase
from club.models import Club
from members.models import Member
from mobile.models import PushSubscription
from notifications import preferences
from notifications.dispatch import dispatch_to_user
from notifications.models import Notification, NotificationCategory, NotificationDelivery
from notifications.services import notify_members

User = get_user_model()
Category = NotificationCategory


class RecordingGateway(PushGateway):
    published: list = []

    def publish(self, device, *, title, body, data, badge=None, thread_id=None, policy=None, collapse_id=None):
        RecordingGateway.published.append(device.installation_id)
        return True


@override_settings(APP_PUSH_GATEWAY="notifications.test_preferences_enforcement.RecordingGateway", VAPID_PRIVATE_KEY="test-key", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PreferenceEnforcementTests(AppApiTestCase):
    """``self.user`` (parent, with a phone) is the guardian of ``self.child`` (no login of their own)."""

    def setUp(self):
        super().setUp()
        RecordingGateway.published = []
        self.phone = PushDevice.objects.create(user=self.user, installation_id="parent-phone", platform="ios", push_token="t")
        # A second account with only a browser, and a third with a phone and a browser.
        self.web_user = User.objects.create_user(email="web@example.com", password="pw-secret-123")
        self.web_member = Member.objects.create(user=self.web_user, first_name="Wanda", last_name="Web")
        self.subscription = PushSubscription.objects.create(club=self.club, member=self.web_member, endpoint="https://fcm.googleapis.com/fcm/send/web", p256dh="k1", auth="k2")

    def run_dispatch(self, *members, category=Category.NEWS):
        """notify_members runs the dispatch eagerly in tests; returns (notifications, webpush mock)."""
        with patch("mobile.services.push.webpush") as webpush:
            notifications = notify_members(members, club=self.club, title="Hi", body="There", category=category)
        return notifications, webpush

    def mute(self, user, channel, *, club=None, category=Category.NEWS, enabled=False):
        preferences.set_preference(user, club, category, channel, enabled)

    def test_everything_is_on_by_default(self):
        _n, webpush = self.run_dispatch(self.member, self.web_member)

        self.assertEqual(RecordingGateway.published, ["parent-phone"])
        self.assertEqual(webpush.call_count, 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_push_muted_skips_native_but_keeps_inbox_and_email(self):
        self.mute(self.user, "push")

        notifications, webpush = self.run_dispatch(self.member)

        self.assertEqual(RecordingGateway.published, [])
        webpush.assert_not_called()
        self.assertEqual(Notification.objects.filter(pk=notifications[0].pk).count(), 1)
        self.assertEqual([m.to for m in mail.outbox], [["parent@example.com"]])
        self.assertFalse(NotificationDelivery.objects.filter(channel__in=["native", "web"]).exists())

    def test_push_muted_skips_web_too_and_the_native_dedupe_still_holds(self):
        # An account with a phone *and* a browser that muted push: neither gets it.
        PushSubscription.objects.create(club=self.club, member=self.member, endpoint="https://fcm.googleapis.com/fcm/send/parent", p256dh="k1", auth="k2")
        self.mute(self.user, "push")

        _n, webpush = self.run_dispatch(self.member)

        self.assertEqual(RecordingGateway.published, [])
        webpush.assert_not_called()

    def test_without_the_mute_the_phone_still_suppresses_the_browser(self):
        PushSubscription.objects.create(club=self.club, member=self.member, endpoint="https://fcm.googleapis.com/fcm/send/parent", p256dh="k1", auth="k2")

        _n, webpush = self.run_dispatch(self.member)

        self.assertEqual(RecordingGateway.published, ["parent-phone"])
        webpush.assert_not_called()

    def test_push_muted_skips_web_only_accounts(self):
        self.mute(self.web_user, "push")

        _n, webpush = self.run_dispatch(self.web_member)

        webpush.assert_not_called()
        self.assertEqual(len(mail.outbox), 1)

    def test_email_muted_keeps_push_but_drops_that_address(self):
        self.mute(self.web_user, "email")
        self.mute(self.user, "email")

        _n, webpush = self.run_dispatch(self.member, self.web_member)

        self.assertEqual(RecordingGateway.published, ["parent-phone"])
        self.assertEqual(webpush.call_count, 1)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(NotificationDelivery.objects.filter(channel="email").exists())

    def test_other_accounts_and_categories_are_unaffected(self):
        self.mute(self.web_user, "push")
        self.mute(self.web_user, "email")
        self.mute(self.user, "push", category=Category.SHOP)

        _n, webpush = self.run_dispatch(self.member, self.web_member)

        self.assertEqual(RecordingGateway.published, ["parent-phone"])
        webpush.assert_not_called()
        self.assertEqual([m.to for m in mail.outbox], [["parent@example.com"]])

    def test_a_club_row_beats_the_all_clubs_row_in_both_directions(self):
        # all clubs: off, this club: on -> delivered
        self.mute(self.user, "push", club=None)
        self.mute(self.user, "push", club=self.club, enabled=True)
        self.run_dispatch(self.member)
        self.assertEqual(RecordingGateway.published, ["parent-phone"])

        # all clubs: on, this club: off -> skipped
        RecordingGateway.published = []
        self.mute(self.user, "push", club=None, enabled=True)
        self.mute(self.user, "push", club=self.club, enabled=False)
        self.run_dispatch(self.member)
        self.assertEqual(RecordingGateway.published, [])

    def test_email_club_row_beats_the_all_clubs_row(self):
        self.mute(self.user, "email", club=None)
        self.mute(self.user, "email", club=self.club, enabled=True)
        self.run_dispatch(self.member)
        self.assertEqual(len(mail.outbox), 1)

        mail.outbox.clear()
        self.mute(self.user, "email", club=None, enabled=True)
        self.mute(self.user, "email", club=self.club, enabled=False)
        self.run_dispatch(self.member)
        self.assertEqual(mail.outbox, [])

    def test_a_muting_in_another_club_does_not_apply(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        self.mute(self.user, "push", club=other)

        self.run_dispatch(self.member)

        self.assertEqual(RecordingGateway.published, ["parent-phone"])

    def test_the_guardians_own_preference_decides_for_the_childs_notification(self):
        self.mute(self.user, "push")
        self.mute(self.user, "email")

        self.run_dispatch(self.child)

        self.assertEqual(RecordingGateway.published, [])
        self.assertEqual(mail.outbox, [])

    def test_the_guardian_is_notified_when_only_the_childs_category_is_on(self):
        self.mute(self.user, "push", category=Category.SHOP)

        self.run_dispatch(self.child, category=Category.NEWS)

        self.assertEqual(RecordingGateway.published, ["parent-phone"])
        self.assertEqual(len(mail.outbox), 1)

    def test_mail_with_attachments_ignores_the_preference(self):
        self.mute(self.user, "email", category=Category.SHOP)

        with patch("mobile.services.push.webpush"):
            notify_members([self.member], club=self.club, title="Invoice", body="Attached", category=Category.SHOP, attachments=[("invoice.pdf", b"%PDF", "application/pdf")])

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].attachments[0][0], "invoice.pdf")

    def test_game_pushes_respect_the_game_category(self):
        texts = lambda language: ("Title", "Body")  # noqa: E731
        kwargs = {"club": self.club, "user": self.user, "texts": texts, "data": {}, "url": "/app/", "category": Category.GAME}

        with patch("mobile.services.push.webpush"):
            self.assertEqual(dispatch_to_user(**kwargs).reached["native"], 1)

            self.mute(self.user, "push", category=Category.GAME)
            RecordingGateway.published = []
            result = dispatch_to_user(**kwargs)

        self.assertEqual(result.total, 0)
        self.assertEqual(RecordingGateway.published, [])

    def test_a_muted_game_push_blocks_the_browser_too(self):
        self.mute(self.web_user, "push", category=Category.GAME)

        with patch("mobile.services.push.webpush") as webpush:
            dispatch_to_user(club=self.club, user=self.web_user, texts=lambda language: ("T", "B"), data={}, url="/app/", category=Category.GAME)
            webpush.assert_not_called()
            dispatch_to_user(club=self.club, user=self.web_user, texts=lambda language: ("T", "B"), data={}, url="/app/", category=Category.NEWS)
            self.assertEqual(webpush.call_count, 1)

    def test_other_game_preferences_do_not_mute_game_pushes(self):
        self.mute(self.user, "push", category=Category.NEWS)

        with patch("mobile.services.push.webpush"):
            result = dispatch_to_user(club=self.club, user=self.user, texts=lambda language: ("T", "B"), data={}, url="/app/", category=Category.GAME)

        self.assertEqual(result.reached["native"], 1)

    def test_provider_and_duration_are_recorded(self):
        PushDevice.objects.create(user=self.user, installation_id="parent-pixel", platform="android", push_token="t")
        self.run_dispatch(self.member, self.web_member)

        rows = {row.target: row for row in NotificationDelivery.objects.all()}
        self.assertEqual(rows["parent-phone"].provider, "apns")
        self.assertEqual(rows["parent-pixel"].provider, "fcm")
        web = NotificationDelivery.objects.get(channel="web")
        self.assertEqual(web.provider, "webpush")
        for row in NotificationDelivery.objects.all():
            self.assertIsNotNone(row.duration_ms, row.channel)
            self.assertGreaterEqual(row.duration_ms, 0)
        self.assertEqual({row.provider for row in NotificationDelivery.objects.filter(channel="email")}, {"email"})
