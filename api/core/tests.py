from django.test import override_settings

from api.core.surface import is_public_path
from api.protected.tests import BASE, CLUB_HOST, AppApiTestCase


class SurfaceTests(AppApiTestCase):
    def test_public_and_protected_paths_are_told_apart(self):
        for path in ("/api/v1/news/", "/api/v1/news/some-slug/", "/api/v1/teams/", "/api/v1/teams/3/roster/", "/api/v1/games/live/", "/api/v1/sponsors/", "/api/v1/docs", "/api/v1/openapi.json"):
            with self.subTest(path=path):
                self.assertTrue(is_public_path(path))
        for path in ("/api/v1/me", "/api/v1/teams/3/roster", "/api/v1/teams/3/events", "/api/v1/events/3", "/api/v1/context"):
            with self.subTest(path=path):
                self.assertFalse(is_public_path(path))

    def test_public_routes_need_no_token_protected_routes_do(self):
        self.assertEqual(self.client.get("/api/v1/news/", HTTP_HOST=CLUB_HOST).status_code, 200)
        self.assertError(self.client.get("/api/v1/context", HTTP_HOST=CLUB_HOST), 401, "unauthenticated")

    def test_public_errors_use_the_envelope(self):
        self.assertError(self.client.get("/api/v1/news/", HTTP_HOST=BASE), 404, "not_found")

    def test_both_kinds_of_route_are_in_one_spec(self):
        paths = self.client.get("/api/v1/openapi.json", HTTP_HOST=BASE).json()["paths"]
        self.assertIn("/api/v1/news/", paths)
        self.assertIn("/api/v1/me", paths)


class LegacyPrefixTests(AppApiTestCase):
    def test_the_old_app_prefix_still_works_and_is_flagged_deprecated(self):
        token = self.login()
        response = self.client.get("/api/app/v1/me", HTTP_HOST=BASE, HTTP_AUTHORIZATION=f"Bearer {token}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Deprecation"], "true")
        self.assertEqual(response.json(), self.api("get", "/me", token=token).json())

    def test_the_new_prefix_is_not_flagged(self):
        self.assertNotIn("Deprecation", self.client.get("/api/v1/meta", HTTP_HOST=BASE))


@override_settings(API_CORS_ALLOWED_ORIGINS=["https://app.example.com"])
class CorsTests(AppApiTestCase):
    def test_public_routes_answer_any_origin(self):
        response = self.client.get("/api/v1/news/", HTTP_HOST=CLUB_HOST, HTTP_ORIGIN="https://anywhere.example")

        self.assertEqual(response["Access-Control-Allow-Origin"], "*")
        self.assertNotIn("private", response.get("Cache-Control", ""))

    def test_protected_routes_answer_only_configured_origins_and_never_with_credentials(self):
        allowed = self.client.options("/api/v1/me", HTTP_HOST=BASE, HTTP_ORIGIN="https://app.example.com")
        other = self.client.options("/api/v1/me", HTTP_HOST=BASE, HTTP_ORIGIN="https://evil.example")

        self.assertEqual(allowed.status_code, 204)
        self.assertEqual(allowed["Access-Control-Allow-Origin"], "https://app.example.com")
        self.assertIn("Authorization", allowed["Access-Control-Allow-Headers"])
        self.assertNotIn("Access-Control-Allow-Credentials", allowed)
        self.assertNotIn("Access-Control-Allow-Origin", other)

    def test_protected_responses_are_never_cached(self):
        response = self.api("get", "/me", token=self.login())

        self.assertEqual(response["Cache-Control"], "private, no-store")

    @override_settings(API_CORS_ALLOWED_ORIGINS=[])
    def test_no_browser_origin_is_allowed_by_default(self):
        response = self.client.get("/api/v1/meta", HTTP_HOST=BASE, HTTP_ORIGIN="https://app.example.com")

        self.assertNotIn("Access-Control-Allow-Origin", response)
