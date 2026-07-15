import importlib
from unittest import mock

from django.db.utils import OperationalError
from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, clear_url_caches, resolve, reverse

from . import urls


class BrowserReloadUrlTests(SimpleTestCase):
    """django-browser-reload serves an open event stream and injects a script into
    every HTML response, so it must never be mounted outside DEBUG."""

    def reload_urlconf(self):
        importlib.reload(urls)
        clear_url_caches()

    def tearDown(self):
        self.reload_urlconf()  # restore the real (DEBUG=False) urlconf

    def test_the_reload_endpoint_is_mounted_in_debug(self):
        with override_settings(DEBUG=True):
            self.reload_urlconf()

            self.assertEqual(resolve("/__reload__/events/").view_name, "django_browser_reload:events")

    def test_the_reload_endpoint_is_absent_without_debug(self):
        self.reload_urlconf()

        with self.assertRaises(Resolver404):
            resolve("/__reload__/events/")


class HealthCheckTests(SimpleTestCase):
    databases = {"default"}

    def test_it_reports_ok_when_the_database_and_cache_answer(self):
        response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "checks": {"database": True, "cache": True}})

    def test_a_dead_database_is_a_503(self):
        # 200 while the database is unreachable is worse than no health check at all: the load
        # balancer would keep sending traffic to a node that cannot serve a single page.
        with mock.patch("rosterchief.health.connection") as db:
            db.cursor.side_effect = OperationalError("connection refused")

            response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["checks"]["database"])

    def test_a_cache_that_swallows_writes_is_unhealthy(self):
        # Not a ping: a cache that accepts writes and returns nothing would have waffle read
        # every feature flag as unset.
        with mock.patch("rosterchief.health.cache") as broken:
            broken.get.return_value = None

            response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["checks"]["cache"])

    def test_an_unreachable_cache_is_a_503(self):
        # Distinct from the cache that answers wrongly above: here Redis is simply down, and
        # the check must fail rather than raise its way to a 500.
        with mock.patch("rosterchief.health.cache") as down:
            down.set.side_effect = ConnectionError("redis is down")

            response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["checks"]["cache"])

    def test_it_is_never_cached(self):
        response = self.client.get(reverse("healthz"))

        self.assertIn("no-cache", response["Cache-Control"])

    @override_settings(ALLOWED_HOSTS=["example.com", "localhost", "127.0.0.1"])
    def test_it_answers_over_the_loopback(self):
        # The container healthcheck and the deploy probe hit it as 127.0.0.1/localhost, before
        # a proxy supplies a real Host. If ALLOWED_HOSTS rejects those, /healthz 400s and the
        # container is unhealthy forever — which is exactly how the first deploy failed.
        for host in ("127.0.0.1", "localhost"):
            self.assertEqual(self.client.get(reverse("healthz"), HTTP_HOST=host).status_code, 200, host)
