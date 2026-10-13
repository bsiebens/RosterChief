"""Per-child switches, critical priority and quiet hours, through the real pipeline
(notify_members -> channels with a recording gateway)."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from appapi.models import PushDevice
from appapi.push import PushGateway
from appapi.tests import AppApiTestCase
from members.models import FamilyMembership, Member
from mobile.models import PushSubscription
from notifications import delivery_log, preferences, quiet_hours
from notifications.models import Notification, NotificationCategory, NotificationDelivery, NotificationPriority
from notifications.services import muted_push_users, notify_members

User = get_user_model()
Category = NotificationCategory
Status = NotificationDelivery.Status


class RecordingGateway(PushGateway):
    published: list = []

    def publish(self, device, *, title, body, data, badge=None, thread_id=None, policy=None, collapse_id=None):
        RecordingGateway.published.append((device.installation_id, title))
        return True


@override_settings(APP_PUSH_GATEWAY="notifications.test_granular_prefs.RecordingGateway", VAPID_PRIVATE_KEY="test-key", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class GranularPreferenceTests(AppApiTestCase):
    """``self.user`` (parent, one phone) guards ``self.child`` (Kim) and ``self.sibling`` (Sam)."""

    def setUp(self):
        super().setUp()
        RecordingGateway.published = []
        self.phone = PushDevice.objects.create(user=self.user, installation_id="parent-phone", platform="ios", push_token="t")
        self.sibling = Member.objects.create(first_name="Sam", last_name="Parent", date_of_birth=datetime.date(2015, 5, 1))
        family = self.child.family_memberships.get().family
        FamilyMembership.objects.create(family=family, member=self.sibling, role=FamilyMembership.FamilyRole.CHILD)
        self.web_user = User.objects.create_user(email="web@example.com", password="pw-secret-123")
        self.web_member = Member.objects.create(user=self.web_user, first_name="Wanda", last_name="Web")
        self.subscription = PushSubscription.objects.create(club=self.club, member=self.web_member, endpoint="https://fcm.googleapis.com/fcm/send/web", p256dh="k1", auth="k2")

    def notify(self, *members, category=Category.NEWS, priority="normal", title="Hi"):
        with patch("mobile.services.push.webpush") as webpush:
            notifications = notify_members(members, club=self.club, title=title, body="There", category=category, priority=priority)
        return notifications, webpush

    def mute(self, channel="push", *, club=None, member=None, category=Category.NEWS, enabled=False, user=None):
        preferences.set_preference(user or self.user, club, category, channel, enabled, member=member)

    def go_quiet(self, user=None, hours=1):
        now = timezone.now()
        quiet_hours.set_quiet_hours(user or self.user, enabled=True, start=(now - datetime.timedelta(hours=hours)).time(), end=(now + datetime.timedelta(hours=hours)).time(), tz="UTC")

    # -- per child -----------------------------------------------------------------

    def test_a_per_child_mute_only_mutes_that_child(self):
        self.mute(club=self.club, member=self.child)

        self.notify(self.child, title="Kim news")
        self.notify(self.sibling, title="Sam news")

        self.assertEqual(RecordingGateway.published, [("parent-phone", "Sam news")])

    def test_a_per_child_mute_for_email_too(self):
        self.mute("email", club=self.club, member=self.child)

        self.notify(self.child)
        self.assertEqual(mail.outbox, [])
        self.notify(self.sibling)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_per_child_row_beats_the_club_row_in_both_directions(self):
        # club: off, Kim: on -> Kim delivered, Sam muted
        self.mute(club=self.club)
        self.mute(club=self.club, member=self.child, enabled=True)
        self.notify(self.child, title="Kim")
        self.notify(self.sibling, title="Sam")
        self.assertEqual(RecordingGateway.published, [("parent-phone", "Kim")])

        # club: on, Kim: off -> Kim muted
        RecordingGateway.published = []
        self.mute(club=self.club, enabled=True)
        self.mute(club=self.club, member=self.child, enabled=False)
        self.notify(self.child, title="Kim")
        self.notify(self.sibling, title="Sam")
        self.assertEqual(RecordingGateway.published, [("parent-phone", "Sam")])

    def test_a_child_row_for_another_account_does_not_apply(self):
        self.mute(club=self.club, member=self.child, user=self.web_user)
        self.notify(self.child)
        self.assertEqual(len(RecordingGateway.published), 1)

    def test_a_per_child_mute_blocks_the_web_push_too(self):
        parent_sub = PushSubscription.objects.create(club=self.club, member=self.member, endpoint="https://fcm.googleapis.com/fcm/send/p", p256dh="k1", auth="k2")
        self.phone.delete()
        self.mute(club=self.club, member=self.child)
        _n, webpush = self.notify(self.child)
        webpush.assert_not_called()
        _n, webpush = self.notify(self.sibling)
        self.assertEqual(webpush.call_count, 1)
        self.assertIsNotNone(parent_sub)

    def test_the_preference_lookup_is_batched_per_club_and_category(self):
        children = [Member.objects.create(first_name=f"C{i}", last_name="Parent") for i in range(8)]
        for child in children:
            FamilyMembership.objects.create(family=self.child.family_memberships.get().family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        self.mute(club=self.club, member=children[0])
        with patch("mobile.services.push.webpush"):
            notifications = notify_members(children + [self.child], club=self.club, title="Hi", body="There", category=Category.NEWS, send_email=False)
        notifications = list(Notification.objects.filter(pk__in=[n.pk for n in notifications]).select_related("club", "member"))
        with CaptureQueriesContext(connection) as queries:
            muted = muted_push_users(notifications, {n.pk: {self.user.pk} for n in notifications})
        pref_queries = [q for q in queries if "notifications_notificationpreference" in q["sql"]]
        self.assertEqual(len(pref_queries), 1)
        self.assertEqual(muted[(self.club.pk, "news", children[0].pk)], {self.user.pk})
        self.assertEqual(muted[(self.club.pk, "news", children[1].pk)], set())

    # -- critical ------------------------------------------------------------------

    def test_critical_is_stored_and_still_obeys_mutes(self):
        self.mute(club=self.club, member=self.child)
        notifications, _w = self.notify(self.child, priority="critical")
        self.assertEqual(Notification.objects.get(pk=notifications[0].pk).priority, NotificationPriority.CRITICAL)
        self.assertEqual(RecordingGateway.published, [])

        self.notify(self.sibling, priority="critical")
        self.assertEqual(len(RecordingGateway.published), 1)

    def test_critical_bypasses_quiet_hours(self):
        self.go_quiet()
        self.notify(self.child, priority="critical")
        self.assertEqual(len(RecordingGateway.published), 1)
        self.assertFalse(NotificationDelivery.objects.filter(status=Status.HELD).exists())

    # -- quiet hours ---------------------------------------------------------------

    def test_a_push_in_quiet_hours_is_held_then_released_after_the_window(self):
        self.go_quiet()
        end = quiet_hours.quiet_until([self.user.pk])[self.user.pk]

        notifications, _w = self.notify(self.child)

        self.assertEqual(RecordingGateway.published, [])
        held = NotificationDelivery.objects.get(channel="native")
        self.assertEqual((held.status, held.channel, held.attempts, held.next_attempt_at), (Status.HELD, "native", 1, end))
        self.assertEqual(held.payload["title"], "Hi")
        self.assertEqual(len(mail.outbox), 1)  # email is not held

        self.assertEqual(delivery_log.retry_due(now=end - datetime.timedelta(minutes=1)), 0)
        self.assertEqual(RecordingGateway.published, [])

        self.assertEqual(delivery_log.retry_due(now=end + datetime.timedelta(minutes=1)), 1)
        self.assertEqual(RecordingGateway.published, [("parent-phone", "Hi")])
        held.refresh_from_db()
        self.assertEqual((held.status, held.attempts, held.payload), (Status.SENT, 1, {}))
        self.assertIsNotNone(notifications)

    def test_game_pushes_are_dropped_not_held(self):
        self.go_quiet()
        self.notify(self.child, category=Category.GAME)
        self.assertEqual(RecordingGateway.published, [])
        self.assertFalse(NotificationDelivery.objects.filter(channel__in=["native", "web"]).exists())

    def test_a_web_push_in_quiet_hours_is_held_and_released(self):
        self.go_quiet(self.web_user)
        _n, webpush = self.notify(self.web_member)
        webpush.assert_not_called()
        held = NotificationDelivery.objects.get(channel="web")
        self.assertEqual(held.status, Status.HELD)
        self.assertEqual(set(held.payload), {"title", "body", "url"})

        with patch("mobile.services.push.webpush") as webpush:
            delivery_log.retry_due(now=held.next_attempt_at + datetime.timedelta(minutes=1))
        self.assertEqual(webpush.call_count, 1)
        held.refresh_from_db()
        self.assertEqual(held.status, Status.SENT)

    def test_quiet_hours_of_other_accounts_do_not_hold(self):
        self.go_quiet(self.web_user)
        self.notify(self.child)
        self.assertEqual(len(RecordingGateway.published), 1)

    def test_held_rows_expire_after_the_category_ttl(self):
        self.go_quiet()
        self.notify(self.child, category=Category.REMINDER)  # a day
        held = NotificationDelivery.objects.get(channel="native")
        later = held.created + datetime.timedelta(days=2)

        delivery_log.retry_due(now=later)

        held.refresh_from_db()
        self.assertEqual((held.status, held.last_error), (Status.FAILED, "expired in quiet hours"))
        self.assertEqual(RecordingGateway.published, [])

    def test_a_notification_read_before_release_is_not_sent(self):
        self.go_quiet()
        notifications, _w = self.notify(self.child)
        Notification.objects.filter(pk=notifications[0].pk).update(read_at=timezone.now())
        held = NotificationDelivery.objects.get(channel="native")

        delivery_log.retry_due(now=held.next_attempt_at + datetime.timedelta(minutes=1))

        held.refresh_from_db()
        self.assertEqual((held.status, held.last_error), (Status.FAILED, "read before release"))
        self.assertEqual(RecordingGateway.published, [])

    def test_a_per_child_mute_wins_over_quiet_hours_no_held_row(self):
        self.go_quiet()
        self.mute(club=self.club, member=self.child)
        self.notify(self.child)
        self.assertFalse(NotificationDelivery.objects.filter(status=Status.HELD).exists())
