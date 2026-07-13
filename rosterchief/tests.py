import importlib

from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, clear_url_caches, resolve

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
