"""Phase-0 spike (API.md §3, §14): the native-app auth API behaves the way the design needs.

These pin down allauth.headless behaviour we build on, not just our own code -- so an allauth
upgrade that changes token/session semantics fails here, loudly, before it reaches an app.
"""

import datetime

from allauth.account.models import EmailAddress
from allauth.mfa.totp.internal.auth import TOTP, format_hotp_value, generate_totp_secret, hotp_value, yield_hotp_counters_from_time
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from rosterchief.test_support import make_club

User = get_user_model()

BASE = "/api/auth/app/v1"
HOSTS = {"ROSTERCHIEF_BASE_DOMAIN": "rosterchief.app", "ALLOWED_HOSTS": ["rosterchief.app", "ajax-united.rosterchief.app", "testserver"]}


def _current_totp_code(secret):
    counter = next(yield_hotp_counters_from_time())
    return format_hotp_value(hotp_value(secret, counter))


@override_settings(**HOSTS)
class HeadlessAppAuthTests(TestCase):
    password = "correct horse battery staple"

    def setUp(self):
        cache.clear()  # allauth rate limits live in the cache
        self.user = User.objects.create_user(email="parent@example.com", password=self.password)
        EmailAddress.objects.create(user=self.user, email=self.user.email, verified=True, primary=True)

    # -- helpers -------------------------------------------------------------------------

    def _call(self, method, path, data=None, host="rosterchief.app", token=None, session_token=None):
        headers = {}
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        if session_token:
            headers["HTTP_X_SESSION_TOKEN"] = session_token
        caller = getattr(self.client, method)
        if data is None and method != "get":
            # Django's test client would otherwise send the literal body "None", which allauth
            # (correctly) rejects as invalid JSON.
            data = b""
        return caller(f"{BASE}{path}", data=data, content_type="application/json", HTTP_HOST=host, **headers)

    def _login(self, email=None, password=None):
        return self._call("post", "/auth/login", {"email": email or self.user.email, "password": password or self.password})

    def _session_for(self, access_token_response):
        """The server session behind a login -- there is exactly one per test."""
        return Session.objects.get()

    # -- login, tokens ---------------------------------------------------------------------

    def test_password_login_returns_access_and_refresh_tokens(self):
        response = self._login()

        self.assertEqual(response.status_code, 200, response.content)
        meta = response.json()["meta"]
        self.assertTrue(meta["is_authenticated"])
        self.assertIn("access_token", meta)
        self.assertIn("refresh_token", meta)

        session = self._call("get", "/auth/session", token=meta["access_token"])
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json()["data"]["user"]["email"], self.user.email)

    def test_wrong_password_is_rejected(self):
        response = self._login(password="nope")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access_token", response.json().get("meta", {}))

    # -- session lifetime (API.md §16-1, the blocking question) -----------------------------

    def test_app_login_gets_the_long_app_session(self):
        before = timezone.now()
        self._login()

        expires = Session.objects.get().expire_date
        lifetime = datetime.timedelta(seconds=settings.APP_SESSION_LIFETIME)
        self.assertGreaterEqual(expires, before + lifetime - datetime.timedelta(minutes=1))

    def test_web_login_keeps_the_default_session_lifetime(self):
        before = timezone.now()
        response = self.client.post("/accounts/login/", {"login": self.user.email, "password": self.password}, HTTP_HOST="rosterchief.app")
        self.assertEqual(response.status_code, 302)

        expires = Session.objects.get().expire_date
        default = datetime.timedelta(seconds=settings.SESSION_COOKIE_AGE)
        self.assertLess(expires, before + default + datetime.timedelta(minutes=1))

    def test_refresh_rotates_and_slides_the_session_forward(self):
        meta = self._login().json()["meta"]
        session = Session.objects.get()
        # Pretend the session was last saved a month ago.
        Session.objects.filter(pk=session.pk).update(expire_date=session.expire_date - datetime.timedelta(days=30))
        shortened = Session.objects.get().expire_date

        refreshed = self._call("post", "/tokens/refresh", {"refresh_token": meta["refresh_token"]})

        self.assertEqual(refreshed.status_code, 200, refreshed.content)
        data = refreshed.json()["data"]
        self.assertNotEqual(data["refresh_token"], meta["refresh_token"])
        self.assertGreater(Session.objects.get().expire_date, shortened + datetime.timedelta(days=29))
        self.assertEqual(self._call("get", "/auth/session", token=data["access_token"]).status_code, 200)

    def test_a_used_refresh_token_cannot_be_replayed(self):
        refresh_token = self._login().json()["meta"]["refresh_token"]
        self.assertEqual(self._call("post", "/tokens/refresh", {"refresh_token": refresh_token}).status_code, 200)

        replay = self._call("post", "/tokens/refresh", {"refresh_token": refresh_token})

        self.assertEqual(replay.status_code, 400)

    def test_refresh_fails_once_the_session_has_expired(self):
        refresh_token = self._login().json()["meta"]["refresh_token"]
        Session.objects.update(expire_date=timezone.now() - datetime.timedelta(seconds=1))

        response = self._call("post", "/tokens/refresh", {"refresh_token": refresh_token})

        self.assertEqual(response.status_code, 400)

    # -- revocation (stateful validation) ---------------------------------------------------

    def test_logout_kills_the_access_and_refresh_tokens(self):
        meta = self._login().json()["meta"]

        self.assertEqual(self._call("delete", "/auth/session", token=meta["access_token"]).status_code, 401)

        self.assertEqual(self._call("get", "/auth/session", token=meta["access_token"]).status_code, 401)
        self.assertEqual(self._call("post", "/tokens/refresh", {"refresh_token": meta["refresh_token"]}).status_code, 400)

    def test_deleting_the_server_session_revokes_a_live_access_token(self):
        access_token = self._login().json()["meta"]["access_token"]
        Session.objects.all().delete()

        self.assertEqual(self._call("get", "/auth/session", token=access_token).status_code, 401)

    # -- MFA ----------------------------------------------------------------------------------

    def test_totp_user_completes_the_mfa_stage_before_getting_tokens(self):
        secret = generate_totp_secret()
        TOTP.activate(self.user, secret)

        first = self._login()
        self.assertEqual(first.status_code, 401)
        body = first.json()
        self.assertIn("mfa_authenticate", [flow["id"] for flow in body["data"]["flows"]])
        self.assertNotIn("access_token", body["meta"])
        pre_auth_token = body["meta"]["session_token"]

        second = self._call("post", "/auth/2fa/authenticate", {"code": _current_totp_code(secret)}, session_token=pre_auth_token)

        self.assertEqual(second.status_code, 200, second.content)
        self.assertIn("access_token", second.json()["meta"])
        lifetime = datetime.timedelta(seconds=settings.APP_SESSION_LIFETIME)
        self.assertGreater(Session.objects.get().expire_date, timezone.now() + lifetime - datetime.timedelta(minutes=1))

    def test_unverified_email_blocks_login_with_a_verify_flow(self):
        EmailAddress.objects.filter(user=self.user).update(verified=False)

        response = self._login()

        self.assertEqual(response.status_code, 401)
        self.assertIn("verify_email", [flow["id"] for flow in response.json()["data"]["flows"]])

    # -- hardening ----------------------------------------------------------------------------

    def test_signup_is_closed(self):
        config = self._call("get", "/config").json()["data"]
        self.assertFalse(config["account"]["is_open_for_signup"])

        response = self._call("post", "/auth/signup", {"email": "stranger@example.com", "password": "another long password 123"})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email="stranger@example.com").exists())

    def test_auth_api_is_not_served_on_a_club_subdomain(self):
        make_club()

        self.assertEqual(self._call("get", "/config", host="ajax-united.rosterchief.app").status_code, 404)

    def test_no_cors_headers_on_the_auth_api(self):
        response = self.client.get(f"{BASE}/config", HTTP_HOST="rosterchief.app", HTTP_ORIGIN="https://evil.example")

        self.assertFalse([header for header in response.headers if header.lower().startswith("access-control-")])
