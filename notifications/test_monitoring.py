from django.test import SimpleTestCase

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
