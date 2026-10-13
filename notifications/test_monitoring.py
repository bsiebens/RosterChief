from django.test import SimpleTestCase, TestCase

from notifications import monitoring


class MonitoringRulesTests(SimpleTestCase):
    def test_health_states(self):
        self.assertEqual(monitoring.health_state(4, 0.0), monitoring.QUIET)
        self.assertEqual(monitoring.health_state(100, 99.0), monitoring.OK)
        self.assertEqual(monitoring.health_state(100, 95.0), monitoring.OK)
        self.assertEqual(monitoring.health_state(100, 94.9), monitoring.DEGRADED)
        self.assertEqual(monitoring.health_state(100, 50.0), monitoring.DEGRADED)
        self.assertEqual(monitoring.health_state(100, 49.9), monitoring.DOWN)
        self.assertEqual(monitoring.health_state(10, None), monitoring.QUIET)

    def test_percentile_nearest_rank(self):
        self.assertIsNone(monitoring.percentile([], 0.5))
        self.assertEqual(monitoring.percentile(list(range(1, 101)), 0.5), 50)
        self.assertEqual(monitoring.percentile(list(range(1, 101)), 0.95), 95)

    def test_mask_target(self):
        self.assertEqual(monitoring.mask_target("email", "alice@example.com"), "a***@example.com")
        self.assertEqual(monitoring.mask_target("native", "x" * 40), "x" * 24 + "…")


class HeldDeliveriesTests(TestCase):
    """A push held for someone's quiet hours is not a send attempt: it must not count as a failure."""

    def test_held_rows_do_not_count_in_provider_health(self):
        from django.utils import timezone

        from rosterchief.test_support import make_club

        from .models import NotificationDelivery

        club = make_club(name="Ajax United", slug="ajax-united")
        for status in (NotificationDelivery.Status.SENT, NotificationDelivery.Status.HELD, NotificationDelivery.Status.HELD):
            NotificationDelivery.objects.create(club=club, channel="native", provider="apns", target="x", status=status)

        (apns,) = [card for card in monitoring.provider_health(timezone.now() - timezone.timedelta(hours=1)) if card["provider"] == "apns"]

        self.assertEqual(apns["requests"], 1)
