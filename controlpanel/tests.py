"""Tests for the platform user lookup (``/users``): search, detail, reset 2FA / password, block / unblock."""

import datetime
import json
from types import SimpleNamespace
from unittest import mock

from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_tenants.utils import schema_context
from ninja.testing import TestClient
from tenant_users.permissions.models import UserTenantPermissions

from club.models import Club, Domain
from club.services import create_club, grant_club_admin, grant_login
from controlpanel.api.urls import api
from controlpanel.models import AccountBlock, PlatformAuditLog
from members.models import Member

User = get_user_model()
PUBLIC = SimpleNamespace(schema_name="public")
META = {"REMOTE_ADDR": "127.0.0.1", "SERVER_NAME": "testserver", "SERVER_PORT": "80"}  # allauth builds links from the request host
PASSWORD = "S3cret-pass-phrase!"


def make_user(email, *, first="Ann", last="Example", level=None, active=True, verified=True, password=PASSWORD):
    user = User.objects.create(email=email, first_name=first, last_name=last, is_active=active)
    if password:
        user.set_password(password)
        user.save(update_fields=["password"])
    else:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    EmailAddress.objects.create(user=user, email=email, primary=True, verified=verified)
    if level:
        UserTenantPermissions.objects.create(profile=user, is_staff=True, is_superuser=level == "superuser")
    return user


def add_totp(user):
    return Authenticator.objects.create(user=user, type=Authenticator.Type.TOTP, data={"secret": "ENCRYPTED-TOTP-SECRET"})


def add_webauthn(user, name="YubiKey"):
    return Authenticator.objects.create(user=user, type=Authenticator.Type.WEBAUTHN, data={"name": name, "credential": {"id": "CRED-ID-SECRET", "public_key": "PK"}})


def add_recovery(user, used_mask=0b11):
    return Authenticator.objects.create(user=user, type=Authenticator.Type.RECOVERY_CODES, data={"seed": "ENCRYPTED-SEED", "used_mask": used_mask})


def login_session(user):
    """A real session for ``user`` like allauth's login creates; its key is the headless X-Session-Token."""
    s = SessionStore()
    s[SESSION_KEY] = user._meta.pk.value_to_string(user)
    s[BACKEND_SESSION_KEY] = "features.backends.FeatureAwareAuthenticationBackend"
    s[HASH_SESSION_KEY] = user.get_session_auth_hash()
    s.save()
    return s.session_key


SECRET_STRINGS = ["ENCRYPTED-TOTP-SECRET", "CRED-ID-SECRET", "ENCRYPTED-SEED", "pbkdf2_", PASSWORD, "used_mask", "seed", "secret", "credential", "password"]


class UserApiBase(TestCase):
    """Runs the real ``superuser_auth`` rules; only the session-token lookup is replaced by ``self.current``."""

    def setUp(self):
        cache.clear()
        self.admin = make_user("admin@example.com", first="Ada", last="Admin", level="superuser")
        self.current = self.admin
        for target, result in [("allauth.headless.contrib.ninja.security.XSessionTokenAuth.__call__", lambda s, request: self.current)]:
            p = mock.patch(target, result)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("controlpanel.api.auth.is_local_development", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(api)

    def get(self, path):
        return self.client.get(path, tenant=PUBLIC, META=dict(META), headers={"X-Session-Token": "x"})

    def post(self, path, body=None, headers=None):
        return self.client.post(path, json=body or {}, tenant=PUBLIC, META=dict(META), headers={"X-Session-Token": "x", **(headers or {})})

    def error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["error"]["code"], code)
        return response.json()["error"]


class ListTests(UserApiBase):
    def setUp(self):
        super().setUp()
        self.staff = make_user("sam@example.com", first="Sam", last="Staff", level="staff")
        self.plain = make_user("pat.plain@club.test", first="Pat", last="Plain", verified=False)
        self.blocked = make_user("bob@club.test", first="Bob", last="Blocked", active=False)
        add_totp(self.staff)
        add_recovery(self.staff)

    def ids(self, query=""):
        r = self.get("/users" + query)
        self.assertEqual(r.status_code, 200, r.content)
        return {i["email"] for i in r.json()["items"]}

    def test_shape_and_values(self):
        body = self.get("/users?q=sam").json()
        self.assertEqual(body["count"], 1)
        item = body["items"][0]
        self.assertEqual(set(item), {"id", "email", "first_name", "last_name", "is_active", "last_login", "date_joined", "email_verified", "mfa_enabled", "platform_level", "club_count", "blocked"})
        self.assertEqual((item["platform_level"], item["mfa_enabled"], item["email_verified"], item["blocked"], item["club_count"]), ("staff", True, True, False, 0))
        self.assertIsNotNone(item["date_joined"])
        plain = self.get("/users?q=pat").json()["items"][0]
        self.assertEqual((plain["platform_level"], plain["mfa_enabled"], plain["email_verified"], plain["date_joined"]), (None, False, False, None))
        self.assertEqual(self.get("/users?q=bob").json()["items"][0]["blocked"], True)

    def test_search_is_partial_case_insensitive_over_email_and_names(self):
        self.assertEqual(self.ids("?q=PLAIN"), {"pat.plain@club.test"})
        self.assertEqual(self.ids("?q=club.test"), {"pat.plain@club.test", "bob@club.test"})
        self.assertEqual(self.ids("?q=pat%20plain"), {"pat.plain@club.test"})  # every word must match something
        self.assertEqual(self.ids("?q=nobody"), set())

    def test_filters(self):
        self.assertEqual(self.ids("?status=blocked"), {"bob@club.test"})
        self.assertEqual(self.ids("?status=active"), {"admin@example.com", "sam@example.com", "pat.plain@club.test"})
        self.assertEqual(self.ids("?mfa=yes"), {"sam@example.com"})
        self.assertEqual(self.ids("?mfa=no"), {"admin@example.com", "pat.plain@club.test", "bob@club.test"})
        self.assertEqual(self.ids("?level=superuser"), {"admin@example.com"})
        self.assertEqual(self.ids("?level=staff"), {"sam@example.com"})
        self.assertEqual(self.ids("?level=none"), {"pat.plain@club.test", "bob@club.test"})
        self.assertEqual(self.ids("?level=staff&mfa=no"), set())
        self.error(self.get("/users?status=bogus"), 422, "validation_error")

    def test_pagination_is_stable(self):
        for i in range(5):
            make_user(f"p{i}@page.test", first="P", last=f"Page{i}")
        first = self.get("/users?q=page.test&limit=2&offset=0").json()
        second = self.get("/users?q=page.test&limit=2&offset=2").json()
        self.assertEqual((first["count"], len(first["items"]), len(second["items"])), (5, 2, 2))
        self.assertFalse({i["id"] for i in first["items"]} & {i["id"] for i in second["items"]})

    def test_query_count_does_not_grow_with_users(self):
        with CaptureQueriesContext(connection) as few:
            self.get("/users?limit=100")
        for i in range(10):
            make_user(f"n{i}@many.test")
        with CaptureQueriesContext(connection) as many:
            self.get("/users?limit=100")
        self.assertEqual(len(few), len(many))

    def test_club_count_excludes_the_platform_and_counts_clubs(self):
        public = Club(schema_name="public", name="Platform", owner=self.admin)
        public.auto_create_schema = False
        public.save()
        public.user_set.add(self.plain)
        club = Club(schema_name="c_one", name="One", owner=self.admin)
        club.auto_create_schema = False
        club.save()
        club.user_set.add(self.plain)
        self.assertEqual(self.get("/users?q=pat").json()["items"][0]["club_count"], 1)

    def test_list_has_no_secrets(self):
        add_webauthn(self.plain)
        text = self.get("/users").content.decode()
        for s in SECRET_STRINGS[:5]:
            self.assertNotIn(s, text)


class DetailTests(UserApiBase):
    def setUp(self):
        super().setUp()
        self.user = make_user("dana@club.test", first="Dana", last="Detail", level="staff")
        EmailAddress.objects.create(user=self.user, email="dana2@club.test", verified=False, primary=False)
        add_totp(self.user)
        add_webauthn(self.user, "Laptop key")
        add_recovery(self.user, used_mask=0b101)  # 2 of the codes used

    def test_shape(self):
        login_session(self.user)
        PlatformAuditLog.objects.create(actor=self.admin, action="platform_admin.granted", target_type="user", target_id=str(self.user.pk), data={"is_superuser": False})
        PlatformAuditLog.objects.create(actor=self.user, action="club.updated", target_type="club", target_id="x")
        PlatformAuditLog.objects.create(actor=self.admin, action="unrelated", target_type="club", target_id="y")
        r = self.get(f"/users/{self.user.pk}")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(set(body), {"account", "blocked", "mfa", "clubs", "sessions", "recent_activity"})
        acc = body["account"]
        self.assertEqual((acc["email"], acc["platform_level"], acc["has_usable_password"], acc["is_active"]), ("dana@club.test", "staff", True, True))
        self.assertEqual({e["email"]: (e["verified"], e["primary"]) for e in acc["emails"]}, {"dana@club.test": (True, True), "dana2@club.test": (False, False)})
        self.assertIsNotNone(acc["date_joined"])
        self.assertIsNone(body["blocked"])
        mfa = body["mfa"]
        self.assertTrue(mfa["enabled"])
        by_type = {a["type"]: a for a in mfa["authenticators"]}
        self.assertEqual(set(by_type), {"totp", "webauthn", "recovery_codes"})
        self.assertEqual(by_type["webauthn"]["name"], "Laptop key")
        self.assertEqual(by_type["recovery_codes"]["unused_codes"], 8 if self._codes() == 10 else self._codes() - 2)
        self.assertIsNone(by_type["totp"]["unused_codes"])
        self.assertEqual(set(by_type["totp"]), {"type", "name", "created_at", "last_used_at", "unused_codes"})
        self.assertEqual(body["sessions"]["active_count"], 1)
        self.assertIsNotNone(body["sessions"]["last_seen"])
        actions = {a["action"] for a in body["recent_activity"]}
        self.assertEqual(actions, {"platform_admin.granted", "club.updated"})
        self.assertEqual(set(body["recent_activity"][0]), {"id", "created", "action", "actor_email", "target_type", "target_id", "data"})

    def _codes(self):
        from allauth.mfa import app_settings

        return app_settings.RECOVERY_CODE_COUNT

    def test_clubs_roles_owner_and_admin(self):
        owned = create_club("Owned FC", self.user)
        other = create_club("Other FC", self.admin)
        with schema_context(other.schema_name):
            m = Member.objects.create(first_name="Dana", last_name="Detail", email="dana@club.test")
        grant_login(other, m, role_slug="member")
        clubs = {c["club"]["name"]: c for c in self.get(f"/users/{self.user.pk}").json()["clubs"]}
        self.assertEqual(set(clubs), {"Owned FC", "Other FC"})
        self.assertEqual((clubs["Owned FC"]["is_owner"], clubs["Owned FC"]["is_admin"], clubs["Owned FC"]["roles"]), (True, True, ["Owner"]))
        self.assertEqual((clubs["Other FC"]["is_owner"], clubs["Other FC"]["is_admin"], clubs["Other FC"]["roles"]), (False, False, ["Member"]))
        self.assertEqual(set(clubs["Owned FC"]["club"]), {"id", "name", "slug", "archived"})
        self.assertFalse(clubs["Owned FC"]["club"]["archived"])
        grant_club_admin(other, self.user)
        other_club = {c["club"]["name"]: c for c in self.get(f"/users/{self.user.pk}").json()["clubs"]}["Other FC"]
        self.assertTrue(other_club["is_admin"])
        self.assertFalse(other_club["is_owner"])
        self.assertEqual(owned.owner_id, self.user.pk)

    def test_blocked_section(self):
        self.post(f"/users/{self.user.pk}/block", {"reason": "spam"})
        body = self.get(f"/users/{self.user.pk}").json()
        self.assertFalse(body["account"]["is_active"])
        self.assertEqual(body["blocked"]["is_blocked"], True)
        self.assertEqual((body["blocked"]["blocked_by_email"], body["blocked"]["reason"]), ("admin@example.com", "spam"))
        self.assertIsNotNone(body["blocked"]["blocked_at"])
        self.assertIn("user.blocked", {a["action"] for a in body["recent_activity"]})

    def test_inactive_without_a_block_record(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.get(f"/users/{self.user.pk}").json()["blocked"], {"is_blocked": True, "blocked_at": None, "blocked_by_email": None, "reason": None})

    def test_no_secrets_anywhere(self):
        login_session(self.user)
        text = self.get(f"/users/{self.user.pk}").content.decode()
        for s in ["ENCRYPTED-TOTP-SECRET", "CRED-ID-SECRET", "ENCRYPTED-SEED", "pbkdf2_", PASSWORD, "used_mask", "credential", '"password"', "public_key"]:
            self.assertNotIn(s, text)
        self.assertNotIn("phone", text)
        self.assertNotIn("birth", text)

    def test_unknown_user_is_404(self):
        self.error(self.get("/users/00000000-0000-0000-0000-000000000000"), 404, "not_found")

    def test_other_users_sessions_are_not_counted(self):
        login_session(self.user)
        login_session(self.user)
        login_session(self.admin)
        s = SessionStore()
        s["something"] = 1
        s.save()
        self.assertEqual(self.get(f"/users/{self.user.pk}").json()["sessions"]["active_count"], 2)

    def test_expired_sessions_are_not_counted(self):
        key = login_session(self.user)
        Session.objects.filter(session_key=key).update(expire_date=timezone.now() - datetime.timedelta(days=1))
        self.assertEqual(self.get(f"/users/{self.user.pk}").json()["sessions"], {"active_count": 0, "last_seen": None, "truncated": False})

    def test_session_scan_is_bounded(self):
        for _ in range(3):
            login_session(self.user)
        with mock.patch("controlpanel.services.accounts.SESSION_SCAN_LIMIT", 2):
            sessions = self.get(f"/users/{self.user.pk}").json()["sessions"]
        self.assertTrue(sessions["truncated"])
        self.assertLessEqual(sessions["active_count"], 2)


class ResetMfaTests(UserApiBase):
    def setUp(self):
        super().setUp()
        self.user = make_user("mia@club.test", first="Mia", last="Mfa", level="staff")
        add_totp(self.user)
        add_webauthn(self.user)
        add_recovery(self.user)
        self.url = f"/users/{self.user.pk}/reset-2fa"

    def test_removes_every_authenticator_and_nothing_else(self):
        key = login_session(self.user)
        hashed = User.objects.get(pk=self.user.pk).password
        r = self.post(self.url, {"reason": "Lost phone"})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["removed"], ["recovery_codes", "totp", "webauthn"])
        self.assertTrue(body["notified"])
        self.assertIn("enrol", body["note"])
        self.assertFalse(Authenticator.objects.filter(user=self.user).exists())
        fresh = User.objects.get(pk=self.user.pk)
        self.assertEqual((fresh.password, fresh.is_active), (hashed, True))
        self.assertTrue(Session.objects.filter(session_key=key).exists())  # sessions are untouched
        self.assertEqual(self.get(f"/users/{self.user.pk}").json()["mfa"], {"enabled": False, "authenticators": []})

    def test_other_users_authenticators_are_kept(self):
        other = make_user("other@club.test")
        add_totp(other)
        self.post(self.url, {"reason": "x"})
        self.assertTrue(Authenticator.objects.filter(user=other).exists())

    def test_audit_and_email(self):
        self.post(self.url, {"reason": "Lost phone"})
        entry = PlatformAuditLog.objects.get(action="user.mfa_reset")
        self.assertEqual((entry.actor, entry.target_type, entry.target_id), (self.admin, "user", str(self.user.pk)))
        self.assertEqual(entry.data, {"removed": ["recovery_codes", "totp", "webauthn"], "reason": "Lost phone", "notified": True})
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["mia@club.test"])
        self.assertIn("two-factor authentication was reset", message.subject + message.body)
        self.assertNotIn("http", message.body)

    def test_notify_false_sends_nothing(self):
        r = self.post(self.url, {"reason": "x", "notify": False})
        self.assertFalse(r.json()["notified"])
        self.assertEqual(mail.outbox, [])
        self.assertFalse(PlatformAuditLog.objects.get(action="user.mfa_reset").data["notified"])

    def test_mail_failure_is_not_fatal(self):
        with mock.patch("controlpanel.services.accounts.send_message", side_effect=OSError("smtp down")):
            r = self.post(self.url, {"reason": "x"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["notified"])
        self.assertFalse(Authenticator.objects.filter(user=self.user).exists())

    def test_refusals(self):
        self.error(self.post(f"/users/{self.admin.pk}/reset-2fa", {"reason": "x"}), 409, "user_action_refused")  # yourself
        Authenticator.objects.filter(user=self.user).delete()
        err = self.error(self.post(self.url, {"reason": "x"}), 409, "user_action_refused")
        self.assertIn("Nothing to reset", err["message"])
        self.assertFalse(PlatformAuditLog.objects.filter(action="user.mfa_reset").exists())

    def test_self_refusal_keeps_own_mfa(self):
        add_totp(self.admin)
        self.error(self.post(f"/users/{self.admin.pk}/reset-2fa", {"reason": "x"}), 409, "user_action_refused")
        self.assertTrue(Authenticator.objects.filter(user=self.admin).exists())

    def test_reason_is_required_and_bounded(self):
        self.error(self.post(self.url, {}), 422, "validation_error")
        self.error(self.post(self.url, {"reason": "   "}), 422, "validation_error")
        self.error(self.post(self.url, {"reason": "x" * 301}), 422, "validation_error")
        self.assertTrue(Authenticator.objects.filter(user=self.user).exists())

    def test_idempotency_key_replays(self):
        first = self.post(self.url, {"reason": "x"}, headers={"Idempotency-Key": "k1"})
        again = self.post(self.url, {"reason": "x"}, headers={"Idempotency-Key": "k1"})
        self.assertEqual((first.status_code, again.status_code), (200, 200))
        self.assertEqual(first.json(), again.json())
        self.assertEqual(PlatformAuditLog.objects.filter(action="user.mfa_reset").count(), 1)

    def test_a_superuser_may_reset_another_superuser(self):
        other = make_user("boss@example.com", level="superuser")
        add_totp(other)
        self.assertEqual(self.post(f"/users/{other.pk}/reset-2fa", {"reason": "x"}).status_code, 200)


class ResetPasswordTests(UserApiBase):
    def setUp(self):
        super().setUp()
        self.user = make_user("rob@club.test", first="Rob", last="Reset")
        self.url = f"/users/{self.user.pk}/reset-password"

    def test_sends_the_reset_email_and_returns_no_token(self):
        r = self.post(self.url, {"reason": "forgot"})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(set(body), {"sent_to", "note"})
        self.assertEqual(body["sent_to"], "rob@club.test")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["rob@club.test"])
        link = next(w for w in mail.outbox[0].body.split() if "/key/" in w or "key=" in w or ("reset" in w and "http" in w))
        token_part = link.rstrip("/").rsplit("/", 1)[-1]  # allauth's own page ends the link with a slash
        self.assertTrue(token_part)
        self.assertNotIn(token_part, r.content.decode())
        self.assertNotIn("key", set(body))

    def test_defaults_sign_the_user_out_and_keep_the_password(self):
        key = login_session(self.user)
        hashed = User.objects.get(pk=self.user.pk).password
        self.post(self.url, {})
        self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.assertEqual(User.objects.get(pk=self.user.pk).password, hashed)
        entry = PlatformAuditLog.objects.get(action="user.password_reset")
        self.assertEqual(entry.data, {"reason": "", "invalidate_sessions": True, "disable_current_password": False})
        self.assertEqual((entry.target_id, entry.actor), (str(self.user.pk), self.admin))

    def test_keep_sessions(self):
        key = login_session(self.user)
        self.post(self.url, {"invalidate_sessions": False})
        self.assertTrue(Session.objects.filter(session_key=key).exists())
        self.assertFalse(PlatformAuditLog.objects.get(action="user.password_reset").data["invalidate_sessions"])

    def test_disable_current_password_and_the_link_still_works(self):
        from allauth.account.forms import default_token_generator

        r = self.post(self.url, {"disable_current_password": True, "invalidate_sessions": False, "reason": "compromised"})
        self.assertEqual(r.status_code, 200)
        user = User.objects.get(pk=self.user.pk)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.check_password(PASSWORD))
        self.assertTrue(PlatformAuditLog.objects.get(action="user.password_reset").data["disable_current_password"])
        words = [w.strip("<>()\"'").rstrip("/") for w in mail.outbox[0].body.split() if "http" in w or "/" in w]
        key = next(w.rsplit("/", 1)[-1] for w in words if w.rsplit("/", 1)[-1].count("-") >= 1)
        _uid, _, token = key.partition("-")
        self.assertTrue(default_token_generator.check_token(user, token), "the emailed token must be made after the password was disabled")

    def test_email_failure_changes_nothing(self):
        with mock.patch("controlpanel.services.accounts.send_password_setup_email", side_effect=OSError("smtp down")), self.assertRaises(OSError):
            self.post(self.url, {"disable_current_password": True})
        self.assertTrue(User.objects.get(pk=self.user.pk).has_usable_password())

    def test_refusals(self):
        self.error(self.post(f"/users/{self.admin.pk}/reset-password", {}), 409, "user_action_refused")  # yourself
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        err = self.error(self.post(self.url, {}), 409, "user_action_refused")
        self.assertIn("blocked", err["message"])
        self.assertEqual(mail.outbox, [])
        self.assertFalse(PlatformAuditLog.objects.filter(action="user.password_reset").exists())

    def test_reason_too_long(self):
        self.error(self.post(self.url, {"reason": "x" * 301}), 422, "validation_error")


class BlockTests(UserApiBase):
    def setUp(self):
        super().setUp()
        self.user = make_user("vic@club.test", first="Vic", last="Victim")
        self.url = f"/users/{self.user.pk}"

    def test_block_deactivates_signs_out_and_records_history(self):
        keys = [login_session(self.user), login_session(self.user)]
        other = login_session(self.admin)
        r = self.post(f"{self.url}/block", {"reason": "Abuse"})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body["user_id"], body["is_active"], body["sessions_ended"]), (str(self.user.pk), False, 2))
        self.assertEqual((body["blocked"]["is_blocked"], body["blocked"]["reason"], body["blocked"]["blocked_by_email"]), (True, "Abuse", "admin@example.com"))
        self.assertFalse(User.objects.get(pk=self.user.pk).is_active)
        self.assertFalse(Session.objects.filter(session_key__in=keys).exists())
        self.assertTrue(Session.objects.filter(session_key=other).exists())
        row = AccountBlock.objects.get(user=self.user)
        self.assertEqual((row.blocked_by, row.reason, row.unblocked_at), (self.admin, "Abuse", None))
        entry = PlatformAuditLog.objects.get(action="user.blocked")
        self.assertEqual((entry.target_id, entry.actor, entry.data["reason"], entry.data["sessions_ended"]), (str(self.user.pk), self.admin, "Abuse", 2))

    def test_unblock_closes_the_block_and_a_second_block_starts_a_new_row(self):
        self.post(f"{self.url}/block", {"reason": "first"})
        r = self.post(f"{self.url}/unblock", {"reason": "mistake"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["is_active"], r.json()["blocked"]), (True, None))
        self.assertTrue(User.objects.get(pk=self.user.pk).is_active)
        row = AccountBlock.objects.get()
        self.assertEqual((row.unblocked_by, row.unblock_reason), (self.admin, "mistake"))
        self.assertIsNotNone(row.unblocked_at)
        self.assertEqual(PlatformAuditLog.objects.get(action="user.unblocked").data, {"reason": "mistake"})
        self.post(f"{self.url}/block", {"reason": "second"})
        rows = list(AccountBlock.objects.order_by("blocked_at"))
        self.assertEqual([(b.reason, b.unblocked_at is None) for b in rows], [("first", False), ("second", True)])
        self.assertEqual(self.post(f"{self.url}/unblock", {}).status_code, 200)  # reason is optional

    def test_refusals(self):
        self.error(self.post(f"/users/{self.admin.pk}/block", {"reason": "x"}), 409, "user_action_refused")
        self.assertTrue(User.objects.get(pk=self.admin.pk).is_active)
        self.error(self.post(f"{self.url}/unblock", {}), 409, "user_action_refused")  # not blocked
        self.post(f"{self.url}/block", {"reason": "x"})
        err = self.error(self.post(f"{self.url}/block", {"reason": "again"}), 409, "user_action_refused")
        self.assertIn("already blocked", err["message"])
        self.assertEqual(AccountBlock.objects.count(), 1)
        self.assertEqual(PlatformAuditLog.objects.filter(action="user.blocked").count(), 1)

    def test_reason_required(self):
        self.error(self.post(f"{self.url}/block", {}), 422, "validation_error")
        self.error(self.post(f"{self.url}/block", {"reason": ""}), 422, "validation_error")
        self.error(self.post(f"{self.url}/block", {"reason": "x" * 301}), 422, "validation_error")
        self.assertTrue(User.objects.get(pk=self.user.pk).is_active)

    def test_last_active_superuser_cannot_be_blocked(self):
        from controlpanel.services import accounts as svc

        helper = make_user("helper@example.com", level="staff")  # a (non-superuser) actor, as in a race between two superusers
        with self.assertRaisesMessage(svc.UserActionRefused, "last active superuser"):
            svc.block(helper, self.admin, "x")
        self.assertTrue(User.objects.get(pk=self.admin.pk).is_active)

    def test_another_superuser_can_be_blocked_while_one_remains(self):
        boss = make_user("boss@example.com", level="superuser")
        self.assertEqual(self.post(f"/users/{boss.pk}/block", {"reason": "left"}).status_code, 200)
        # now the actor is the last active superuser, and blocking themselves is refused as "self"
        self.error(self.post(f"/users/{self.admin.pk}/block", {"reason": "x"}), 409, "user_action_refused")

    def test_blocked_users_are_excluded_from_active_superuser_count(self):
        from controlpanel.services import accounts as svc

        boss = make_user("boss@example.com", level="superuser", active=False)
        helper = make_user("helper@example.com", level="staff")
        with self.assertRaises(svc.UserActionRefused):
            svc.block(helper, self.admin, "x")
        self.assertEqual(boss.is_active, False)

    def test_idempotent(self):
        a = self.post(f"{self.url}/block", {"reason": "x"}, headers={"Idempotency-Key": "b1"})
        b = self.post(f"{self.url}/block", {"reason": "x"}, headers={"Idempotency-Key": "b1"})
        self.assertEqual((a.status_code, b.status_code), (200, 200))
        self.assertEqual(AccountBlock.objects.count(), 1)


class PermissionTests(UserApiBase):
    def test_staff_gets_403_everywhere_and_nothing_changes(self):
        staff = make_user("staff@example.com", level="staff")
        target = make_user("t@club.test")
        add_totp(target)
        self.current = staff
        calls = [
            ("get", "/users"),
            ("get", f"/users/{target.pk}"),
            ("post", f"/users/{target.pk}/reset-2fa", {"reason": "x"}),
            ("post", f"/users/{target.pk}/reset-password", {}),
            ("post", f"/users/{target.pk}/block", {"reason": "x"}),
            ("post", f"/users/{target.pk}/unblock", {}),
        ]
        for method, path, *body in calls:
            r = self.get(path) if method == "get" else self.post(path, *body)
            self.error(r, 403, "forbidden")
        self.assertTrue(Authenticator.objects.filter(user=target).exists())
        self.assertTrue(User.objects.get(pk=target.pk).is_active)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(PlatformAuditLog.objects.filter(action__startswith="user.").exists())

    def test_anonymous_gets_401(self):
        self.current = None
        self.assertEqual(self.get("/users").status_code, 401)

    def test_nonplatform_user_gets_403(self):
        self.current = make_user("nobody@club.test")
        self.error(self.get("/users"), 403, "forbidden")


class BlockedAccountLockoutTests(TestCase):
    """Real sign-in and real session tokens: no patching of allauth or PlatformAuth."""

    def setUp(self):
        cache.clear()
        self.admin = make_user("admin@example.com", level="superuser")
        self.boss = make_user("boss@example.com", level="superuser")
        self.victim = make_user("victim@club.test")
        public = Club(schema_name="public", name="Platform", owner=self.admin)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        p = mock.patch("controlpanel.api.auth.is_local_development", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(api)

    def me(self, token):
        return self.client.get("/me", tenant=PUBLIC, META=dict(META), headers={"X-Session-Token": token})

    def login(self, email):
        return Client().post("/_allauth/app/v1/auth/login", data=json.dumps({"email": email, "password": PASSWORD}), content_type="application/json", HTTP_HOST="testserver")

    def test_the_headless_login_harness_works_for_active_users(self):
        r = self.login("victim@club.test")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["meta"]["is_authenticated"])
        self.assertTrue(r.json()["meta"]["session_token"])

    def test_headless_login_preserves_password_whitespace(self):
        password = f"  {PASSWORD} "
        self.victim.set_password(password)
        self.victim.save(update_fields=["password"])
        self.assertEqual(self.login("victim@club.test").status_code, 400)
        response = Client().post(
            "/_allauth/app/v1/auth/login",
            data={"email": "victim@club.test", "password": password},
            content_type="application/json",
            HTTP_HOST="testserver",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["meta"]["session_token"])

    def test_blocked_user_cannot_sign_in(self):
        self.client.post(f"/users/{self.victim.pk}/block", json={"reason": "x"}, tenant=PUBLIC, META=dict(META), headers={"X-Session-Token": login_session(self.admin)})
        self.assertFalse(User.objects.get(pk=self.victim.pk).is_active)
        r = self.login("victim@club.test")
        self.assertNotEqual(r.status_code, 200, r.content)
        self.assertNotIn("session_token", r.content.decode())
        self.assertFalse(r.json().get("meta", {}).get("is_authenticated", False))
        self.assertEqual(self.login("admin@example.com").status_code, 200)  # others are unaffected

    def test_existing_token_is_rejected_after_blocking_via_the_api(self):
        admin_token = login_session(self.admin)
        token = login_session(self.boss)
        self.assertEqual(self.me(token).status_code, 200)
        r = self.client.post(f"/users/{self.boss.pk}/block", json={"reason": "x"}, tenant=PUBLIC, META=dict(META), headers={"X-Session-Token": admin_token})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.me(token).status_code, 401)

    def test_a_lingering_session_of_an_inactive_user_is_rejected(self):
        """Defence in depth: even if the session row survives, an inactive account does not authenticate."""
        token = login_session(self.boss)
        self.assertEqual(self.me(token).status_code, 200)
        User.objects.filter(pk=self.boss.pk).update(is_active=False)
        self.assertTrue(Session.objects.filter(session_key=token).exists())
        r = self.me(token)
        self.assertEqual(r.status_code, 401, r.content)

    def test_blocked_token_is_rejected_by_the_club_api_too(self):
        from django.http import HttpRequest

        from api.auth import TokenAuth

        def authenticate(token):
            request = HttpRequest()
            request.META["HTTP_X_SESSION_TOKEN"] = token
            request.META["HTTP_HOST"] = "testserver"
            return TokenAuth()(request)

        token = login_session(self.victim)
        self.assertEqual(authenticate(token), self.victim)
        User.objects.filter(pk=self.victim.pk).update(is_active=False)
        self.assertIsNone(authenticate(token))

    def test_unblock_restores_sign_in(self):
        token = login_session(self.admin)
        self.client.post(f"/users/{self.victim.pk}/block", json={"reason": "x"}, tenant=PUBLIC, META=dict(META), headers={"X-Session-Token": token})
        self.client.post(f"/users/{self.victim.pk}/unblock", json={}, tenant=PUBLIC, META=dict(META), headers={"X-Session-Token": token})
        self.assertEqual(self.login("victim@club.test").status_code, 200)
