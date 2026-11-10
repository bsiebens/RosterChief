"""The push part of the platform API: monitoring, configuration, sandbox, composer and a user's devices.

The engine modules (``notifications.health``, ``gateways``, ``services``, ``preferences``) are patched wherever a test needs exact numbers or must not
touch a provider; when a module does not exist yet a stub stands in so these tests only depend on the documented interface."""

import datetime
import importlib
import sys
import types
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest import mock

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from club.services import create_club
from controlpanel.models import PlatformAuditLog
from controlpanel.services import push_monitoring as mon
from controlpanel.tests import UserApiBase, make_user
from notifications.models import (
    MasterSwitch,
    NotificationDelivery,
    NotificationPreference,
    PushAlert,
    PushDevice,
    PushSettings,
    QuietHours,
    SandboxEvent,
    SandboxMessage,
    SandboxState,
    UserNotificationSettings,
    WebPushSubscription,
)

Status = NotificationDelivery.Status


@contextmanager
def engine(name, **attrs):
    """Patch attributes of ``notifications.<name>``; a stub module stands in when the real one is not there (yet)."""
    try:
        module = importlib.import_module(f"notifications.{name}")
    except ImportError:
        stub = types.ModuleType(f"notifications.{name}")
        for key, value in attrs.items():
            setattr(stub, key, value)
        import notifications

        with mock.patch.dict(sys.modules, {f"notifications.{name}": stub}), mock.patch.object(notifications, name, stub, create=True):
            yield stub
        return
    with mock.patch.multiple(module, create=True, **attrs):
        yield module


def queries(ctx):
    """The statements a request really ran (the tenant connection prefixes each one with ``SET search_path``)."""
    return [q["sql"] for q in ctx.captured_queries if not q["sql"].startswith(("SET search_path", "SAVEPOINT", "RELEASE SAVEPOINT"))]


def real_engine(name):
    try:
        return importlib.import_module(f"notifications.{name}")
    except ImportError:
        return None


def health_stats(**by_provider):
    base = {"requests": 0, "sent": 0, "failed": 0, "dead": 0, "retrying": 0, "success_rate": None, "p50_ms": None, "p95_ms": None, "last_success": None, "last_failure": None, "state": "quiet"}
    return {p: {**base, **v} for p, v in by_provider.items()}


def result(**kw):
    return SimpleNamespace(**{"channel": "native", "provider": "apns", "target": "tok", "target_masked": "tok", "status": "sent", "error": "", "duration_ms": 12, "delivery_id": None, "sandboxed": False, **kw})


class PushBase(UserApiBase):
    def setUp(self):
        super().setUp()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.health_mock = mock.Mock(side_effect=lambda since, club=None: health_stats(apns={"requests": 10, "sent": 9, "failed": 1, "success_rate": 90.0, "state": "degraded"}, fcm={}))
        self.retry_mock = mock.Mock(return_value={"waiting": 2, "oldest_next_attempt": None, "overdue": 0, "stuck": False, "reasons": []})
        self.outbox_mock = mock.Mock(return_value={"pending": 3, "failed": 1, "oldest_pending": None})
        self.stack.enter_context(engine("health", provider_health=self.health_mock, retry_queue=self.retry_mock, outbox_stats=self.outbox_mock))
        self.cred_mock = mock.Mock(side_effect=lambda p: {"configured": p == "apns", "missing": [] if p == "apns" else ["APP_PUSH_FCM_PROJECT_ID"]})
        self.validate_mock = mock.Mock(return_value={"ok": True, "detail": "Key parsed."})
        self.stack.enter_context(engine("gateways", credentials_status=self.cred_mock, validate_credentials=self.validate_mock))
        self.send_mock = mock.Mock(return_value=[result()])
        self.stack.enter_context(engine("services", send_now=self.send_mock))
        self.quiet_mock = mock.Mock(return_value=(False, None))
        self.stack.enter_context(engine("preferences", quiet_state=self.quiet_mock))

    def put(self, path, body):
        return self.client.put(path, json=body, tenant=self._tenant(), META=self._meta(), headers={"X-Session-Token": "x"})

    def patch(self, path, body):
        return self.client.patch(path, json=body, tenant=self._tenant(), META=self._meta(), headers={"X-Session-Token": "x"})

    def delete(self, path):
        return self.client.delete(path, tenant=self._tenant(), META=self._meta(), headers={"X-Session-Token": "x"})

    @staticmethod
    def _tenant():
        from controlpanel.tests import PUBLIC

        return PUBLIC

    @staticmethod
    def _meta():
        from controlpanel.tests import META

        return dict(META)

    def audits(self, action):
        return list(PlatformAuditLog.objects.filter(action=action).order_by("created"))

    def delivery(self, *, status=Status.FAILED, provider="apns", minutes_ago=5, club=None, user=None, target="installation-id-0123456789abcdef-long", error="BadDeviceToken", channel="native"):
        d = NotificationDelivery.objects.create(provider=provider, status=status, channel=channel, target=target, club=club, user=user, last_error=error, title="t")
        NotificationDelivery.objects.filter(pk=d.pk).update(created=timezone.now() - datetime.timedelta(minutes=minutes_ago))
        return d


# --- access -----------------------------------------------------------------------------------------------------------


class AccessTests(PushBase):
    def calls(self):
        user = make_user("target@club.test")
        dev = PushDevice.objects.create(user=user, installation_id="i1", platform="ios", push_token="t")
        some = "00000000-0000-0000-0000-000000000001"
        return [
            ("get", "/push/monitoring", None),
            ("get", "/push/config", None),
            ("patch", "/push/config", {"outbox_max_attempts": 7}),
            ("post", "/push/config/validate/apns", None),
            ("get", "/push/sandbox", None),
            ("put", "/push/sandbox/mode", {"enabled": True, "confirm": True}),
            ("put", "/push/sandbox/scenario", {"scenario": "ok", "latency_ms": 0}),
            ("get", "/push/sandbox/messages", None),
            ("delete", "/push/sandbox/messages", None),
            ("post", "/push/send", {"target": {"type": "user", "id": some}, "title": "t", "body": "b"}),
            ("get", "/push/targets?q=a", None),
            ("get", f"/users/{user.pk}/push", None),
            ("delete", f"/users/{user.pk}/push/devices/{dev.pk}", None),
            ("delete", f"/users/{user.pk}/push/web-subscriptions/{some}", None),
        ]

    def call(self, method, path, body):
        if method == "get":
            return self.get(path)
        if method == "post":
            return self.post(path, body)
        return getattr(self, method)(path, *([body] if method in ("put", "patch") else []))

    def test_staff_gets_403_and_nothing_changes(self):
        calls = self.calls()
        self.current = make_user("staff@example.com", level="staff")
        for method, path, body in calls:
            self.error(self.call(method, path, body), 403, "forbidden")
        self.assertTrue(PushDevice.objects.exists())
        self.assertFalse(PlatformAuditLog.objects.filter(action__startswith="push.").exists())
        self.assertFalse(SandboxState.objects.exists())
        self.send_mock.assert_not_called()

    def test_anonymous_gets_401(self):
        calls = self.calls()
        self.current = None
        for method, path, body in calls:
            self.assertEqual(self.call(method, path, body).status_code, 401, path)

    def test_nonplatform_user_gets_403(self):
        calls = self.calls()
        self.current = make_user("nobody@club.test")
        for method, path, body in calls:
            self.error(self.call(method, path, body), 403, "forbidden")


# --- monitoring -------------------------------------------------------------------------------------------------------


class MonitoringTests(PushBase):
    def test_shape(self):
        body = self.get("/push/monitoring").json()
        self.assertEqual(set(body), {"window", "since", "club_id", "alerts", "providers", "retry_queue", "outbox", "series", "top_failures", "recent_problems", "devices", "sandbox"})
        self.assertEqual(body["window"], "24h")
        self.assertIsNone(body["club_id"])
        self.assertEqual([p["provider"] for p in body["providers"]], ["apns", "fcm", "webpush"])  # no email card: the engine did not report one
        apns, fcm, _web = body["providers"]
        self.assertEqual((apns["label"], apns["configured"], apns["enabled"], apns["requests"], apns["success_rate"], apns["state"]), ("APNs (iOS)", True, True, 10, 90.0, "degraded"))
        self.assertEqual((fcm["configured"], fcm["state"], fcm["requests"]), (False, "quiet", 0))
        self.assertEqual(body["retry_queue"]["waiting"], 2)
        self.assertEqual(body["outbox"], {"pending": 3, "failed": 1, "oldest_pending": None})
        self.assertEqual(body["devices"], {"ios": {"enabled": 0, "disabled": 0, "stale": 0}, "android": {"enabled": 0, "disabled": 0, "stale": 0}, "web": {"subscriptions": 0}})
        self.assertEqual(body["sandbox"], {"enabled": False, "expires_at": None})

    def test_enabled_follows_settings(self):
        row = PushSettings.get()
        row.fcm_enabled = False
        row.save()
        providers = {p["provider"]: p for p in self.get("/push/monitoring").json()["providers"]}
        self.assertEqual((providers["apns"]["enabled"], providers["fcm"]["enabled"]), (True, False))

    def test_email_card_appears_when_engine_reports_it(self):
        self.health_mock.side_effect = lambda since, club=None: health_stats(apns={}, email={"requests": 4, "sent": 4, "success_rate": 100.0, "state": "quiet"})
        providers = {p["provider"]: p for p in self.get("/push/monitoring").json()["providers"]}
        self.assertEqual((providers["email"]["requests"], providers["email"]["configured"], providers["email"]["enabled"]), (4, None, None))

    def test_window_default_and_bad_value(self):
        for query, window, hours in [("", "24h", 24), ("?window=1h", "1h", 1), ("?window=7d", "7d", 168), ("?window=banana", "24h", 24)]:
            body = self.get("/push/monitoring" + query).json()
            self.assertEqual(body["window"], window, query)
            since = datetime.datetime.fromisoformat(body["since"])
            self.assertAlmostEqual((timezone.now() - since).total_seconds(), hours * 3600, delta=30)
            self.assertAlmostEqual((self.health_mock.call_args.args[0] - since).total_seconds(), 0, delta=0.01)

    def test_series_buckets_and_counts(self):
        for window, n in [("1h", 12), ("24h", 24), ("7d", 7)]:
            series = self.get(f"/push/monitoring?window={window}").json()["series"]
            self.assertEqual(len(series), n, window)
            starts = [datetime.datetime.fromisoformat(s["bucket"]) for s in series]
            self.assertEqual(starts, sorted(starts))
            self.assertLessEqual(starts[-1], timezone.now())
        self.delivery(status=Status.SENT, minutes_ago=1)
        self.delivery(status=Status.SENT, minutes_ago=1)
        self.delivery(status=Status.FAILED, minutes_ago=1)
        self.delivery(status=Status.RETRYING, minutes_ago=1)
        self.delivery(status=Status.HELD, minutes_ago=1)  # not counted
        self.delivery(status=Status.SENT, minutes_ago=50)
        self.delivery(status=Status.SENT, minutes_ago=60 * 30)  # outside 24h
        for window, expected in (("1h", (3, 2)), ("24h", (3, 2)), ("7d", (4, 2))):
            series = self.get(f"/push/monitoring?window={window}").json()["series"]
            self.assertEqual((sum(s["sent"] for s in series), sum(s["not_sent"] for s in series)), expected, window)
        # everything from the last minutes sits in the last two buckets (a bucket boundary may fall in between)
        tail = self.get("/push/monitoring?window=1h").json()["series"][-2:]
        self.assertEqual((sum(s["sent"] for s in tail), sum(s["not_sent"] for s in tail)), (2, 2))

    def test_bucket_starts_are_aligned(self):
        now = timezone.make_aware(datetime.datetime(2026, 11, 2, 13, 47, 31))
        h1 = mon.bucket_starts("1h", now)
        self.assertEqual((h1[-1].minute, h1[0].minute, len(h1)), (45, 50, 12))
        h24 = mon.bucket_starts("24h", now)
        self.assertEqual((h24[-1].hour, h24[-1].minute, len(h24)), (13, 0, 24))
        d7 = mon.bucket_starts("7d", now)
        self.assertEqual((d7[-1].day, d7[0].day, d7[-1].hour, len(d7)), (2, 27, 0, 7))

    def test_club_filter(self):
        club = create_club("Filter FC", self.admin)
        other = create_club("Other FC", self.admin)
        self.delivery(club=club, status=Status.FAILED, error="in club")
        self.delivery(club=other, status=Status.FAILED, error="in other")
        self.delivery(club=None, status=Status.FAILED, error="no club")
        everything = self.get("/push/monitoring").json()
        self.assertEqual(len(everything["recent_problems"]), 3)
        body = self.get(f"/push/monitoring?club={club.pk}").json()
        self.assertEqual(body["club_id"], str(club.pk))
        self.assertEqual([p["error"] for p in body["recent_problems"]], ["in club"])
        self.assertEqual([f["error"] for f in body["top_failures"]], ["in club"])
        self.assertEqual(sum(s["not_sent"] for s in body["series"]), 1)
        self.assertEqual(self.health_mock.call_args.kwargs["club"].pk, club.pk)
        self.assertEqual(body["recent_problems"][0]["club"], {"id": str(club.pk), "name": "Filter FC"})

    def test_invalid_or_unknown_club_is_ignored(self):
        self.delivery(error="x")
        for value in ("not-a-uuid", "00000000-0000-0000-0000-000000000009"):
            body = self.get(f"/push/monitoring?club={value}").json()
            self.assertIsNone(body["club_id"])
            self.assertEqual(len(body["recent_problems"]), 1)

    def test_masking(self):
        user = make_user("alice@example.com")
        self.delivery(user=user, target="alice@example.com", provider="email", channel="email")
        self.delivery(user=user, target="x" * 80)
        problems = self.get("/push/monitoring").json()["recent_problems"]
        by_target = {p["target_masked"] for p in problems}
        self.assertIn("a***@example.com", by_target)
        self.assertIn("x" * 23 + "…", by_target)
        self.assertTrue(all(len(t) <= 24 for t in by_target))
        self.assertEqual({p["user_email_masked"] for p in problems}, {"a***@example.com"})
        self.assertNotIn("alice@example.com", self.get("/push/monitoring").content.decode())
        self.assertEqual((mon.mask_email("bo@x.io"), mon.mask_email(None), mon.truncate("short")), ("b***@x.io", None, "short"))

    def test_recent_problems_exclude_sent_and_held_and_are_capped(self):
        self.delivery(status=Status.SENT)
        self.delivery(status=Status.HELD)
        self.delivery(status=Status.DEAD, error="Unregistered")
        for i in range(55):
            self.delivery(status=Status.FAILED, minutes_ago=i)
        problems = self.get("/push/monitoring").json()["recent_problems"]
        self.assertEqual(len(problems), 50)
        self.assertNotIn("sent", {p["status"] for p in problems})
        self.assertNotIn("held", {p["status"] for p in problems})
        created = [p["created"] for p in problems]
        self.assertEqual(created, sorted(created, reverse=True))
        self.assertEqual(set(problems[0]), {"id", "created", "provider", "club", "target_masked", "status", "attempts", "error", "user_id", "user_email_masked"})

    def test_top_failures_grouped_and_ordered(self):
        for _ in range(3):
            self.delivery(error="BadDeviceToken")
        self.delivery(error="Unregistered", status=Status.DEAD)
        self.delivery(error="BadDeviceToken", provider="fcm")
        self.delivery(error="", status=Status.FAILED)
        self.delivery(error="fine", status=Status.SENT)
        top = self.get("/push/monitoring").json()["top_failures"]
        self.assertEqual(next((t["provider"], t["error"], t["count"]) for t in top), ("apns", "BadDeviceToken", 3))
        self.assertEqual(len(top), 3)
        self.assertEqual(set(top[0]), {"provider", "error", "count", "last_seen"})
        for i in range(15):
            self.delivery(error=f"e{i}")
        self.assertEqual(len(self.get("/push/monitoring").json()["top_failures"]), 10)

    def test_alerts_only_open(self):
        PushAlert.objects.create(key="provider:apns", message="APNs is down")
        PushAlert.objects.create(key="provider:fcm", message="old", resolved_at=timezone.now())
        alerts = self.get("/push/monitoring").json()["alerts"]
        self.assertEqual([a["message"] for a in alerts], ["APNs is down"])
        self.assertEqual(set(alerts[0]), {"id", "message", "first_seen", "notifications_sent"})

    def test_device_counts(self):
        user = make_user("dev@club.test")
        old = timezone.now() - datetime.timedelta(days=100)
        mk = lambda i, platform, **kw: PushDevice.objects.create(user=user, installation_id=f"i{i}", platform=platform, push_token=f"t{i}", **kw)  # noqa: E731
        mk(1, "ios")
        stale = mk(2, "ios")
        mk(3, "ios", enabled=False)
        mk(4, "android")
        PushDevice.objects.filter(pk=stale.pk).update(last_seen=old)
        WebPushSubscription.objects.create(user=user, endpoint="https://e/1", p256dh="p", auth="a")
        d = self.get("/push/monitoring").json()["devices"]
        self.assertEqual(d["ios"], {"enabled": 2, "disabled": 1, "stale": 1})
        self.assertEqual(d["android"], {"enabled": 1, "disabled": 0, "stale": 0})
        self.assertEqual(d["web"], {"subscriptions": 1})

    def test_sandbox_block(self):
        self.client.put  # noqa: B018
        self.put("/push/sandbox/mode", {"enabled": True, "confirm": True, "expires_in_minutes": 30})
        s = self.get("/push/monitoring").json()["sandbox"]
        self.assertTrue(s["enabled"])
        self.assertIsNotNone(s["expires_at"])

    def test_query_budget(self):
        user = make_user("q@club.test")
        for i in range(30):
            self.delivery(user=user, status=Status.FAILED, minutes_ago=i, error=f"e{i % 4}")
        with CaptureQueriesContext(connection) as ctx:
            r = self.get("/push/monitoring?window=7d")
        self.assertEqual(r.status_code, 200)
        self.assertLess(len(queries(ctx)), 40, [q[:100] for q in queries(ctx)])

    def test_works_against_the_real_health_module(self):
        if real_engine("health") is None or real_engine("gateways") is None:
            self.skipTest("engine modules not present")
        self.stack.close()  # undo the patches of setUp: the real modules answer
        self.delivery(status=Status.SENT, minutes_ago=2)
        with CaptureQueriesContext(connection) as ctx:
            r = self.get("/push/monitoring")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertLess(len(queries(ctx)), 40, [q[:100] for q in queries(ctx)])
        self.assertEqual({p["provider"] for p in r.json()["providers"]} >= {"apns", "fcm", "webpush"}, True)


# --- configuration ----------------------------------------------------------------------------------------------------


class ConfigTests(PushBase):
    def test_get(self):
        body = self.get("/push/config").json()
        self.assertEqual(set(body), {"providers", "settings", "sandbox_default"})
        self.assertEqual([(p["provider"], p["configured"], p["missing"], p["enabled"]) for p in body["providers"]], [("apns", True, [], True), ("fcm", False, ["APP_PUSH_FCM_PROJECT_ID"], True), ("webpush", False, ["APP_PUSH_FCM_PROJECT_ID"], True)])
        self.assertEqual(
            set(body["settings"]),
            {
                "apns_enabled", "fcm_enabled", "webpush_enabled", "retry_delays_minutes", "outbox_max_attempts", "sent_retention_days", "failed_retention_days", "stale_device_days",
                "disabled_device_retention_days", "unseen_device_retention_days", "degraded_below_percent", "down_below_percent", "health_min_requests", "alert_emails",
            },
        )  # fmt: skip
        self.assertEqual(body["settings"]["retry_delays_minutes"], [1, 5, 30])
        self.assertIsInstance(body["sandbox_default"], bool)
        with override_settings(PUSH_SANDBOX=True):
            self.assertTrue(self.get("/push/config").json()["sandbox_default"])

    def test_patch_updates_audits_and_returns_settings(self):
        r = self.patch("/push/config", {"fcm_enabled": False, "retry_delays_minutes": [2, 10], "down_below_percent": 40, "alert_emails": "a@x.com; b@y.com,  a@x.com"})
        self.assertEqual(r.status_code, 200, r.content)
        s = r.json()
        self.assertEqual((s["fcm_enabled"], s["retry_delays_minutes"], s["down_below_percent"], s["alert_emails"]), (False, [2, 10], 40, "a@x.com, b@y.com"))
        row = PushSettings.get()
        self.assertEqual((row.fcm_enabled, row.retry_delays_minutes, row.apns_enabled), (False, [2, 10], True))
        (entry,) = self.audits("push.config_changed")
        self.assertEqual(sorted(entry.data["changed"]), ["alert_emails", "down_below_percent", "fcm_enabled", "retry_delays_minutes"])
        self.assertEqual(entry.actor, self.admin)
        self.assertEqual(self.get("/push/config").json()["settings"], s)

    def test_patch_without_real_change_is_not_audited(self):
        self.assertEqual(self.patch("/push/config", {"apns_enabled": True}).status_code, 200)
        self.assertEqual(self.patch("/push/config", {}).status_code, 200)
        self.assertFalse(self.audits("push.config_changed"))

    def test_validation(self):
        cases = {
            "retry_delays_minutes": [[], [0], [1441], list(range(1, 12)), ["a"], [1.5], "5", [True]],
            "degraded_below_percent": [101, -1, "x"],
            "down_below_percent": [101, -1],
            "alert_emails": ["nope", "a@x.com, broken"],
            "outbox_max_attempts": [0, 21],
            "apns_enabled": ["yes", 1],
            "sent_retention_days": [0],
        }
        for field, values in cases.items():
            for value in values:
                r = self.patch("/push/config", {field: value})
                err = self.error(r, 422, "validation_error")
                self.assertIn(field, err["fields"], (field, value))
        self.assertFalse(self.audits("push.config_changed"))
        self.assertEqual(PushSettings.get().retry_delays_minutes, [1, 5, 30])

    def test_retry_delays_boundaries_and_unsorted_allowed(self):
        self.assertEqual(self.patch("/push/config", {"retry_delays_minutes": [1440, 1, 30]}).json()["retry_delays_minutes"], [1440, 1, 30])
        self.assertEqual(self.patch("/push/config", {"retry_delays_minutes": list(range(1, 11))}).status_code, 200)

    def test_down_must_be_below_degraded(self):
        err = self.error(self.patch("/push/config", {"down_below_percent": 95}), 422, "validation_error")  # degraded is 95
        self.assertIn("down_below_percent", err["fields"])
        self.error(self.patch("/push/config", {"degraded_below_percent": 40}), 422, "validation_error")  # down is 50
        self.assertEqual(self.patch("/push/config", {"degraded_below_percent": 80, "down_below_percent": 20}).status_code, 200)
        self.assertEqual(self.patch("/push/config", {"degraded_below_percent": 100, "down_below_percent": 0}).status_code, 200)
        self.assertEqual(self.patch("/push/config", {"alert_emails": ""}).json()["alert_emails"], "")

    def test_one_bad_field_saves_nothing(self):
        self.error(self.patch("/push/config", {"outbox_max_attempts": 9, "alert_emails": "bad"}), 422, "validation_error")
        self.assertEqual(PushSettings.get().outbox_max_attempts, 5)

    def test_validate_credentials(self):
        r = self.post("/push/config/validate/apns")
        self.assertEqual((r.status_code, r.json()), (200, {"ok": True, "detail": "Key parsed."}))
        self.validate_mock.assert_called_once_with("apns")
        (entry,) = self.audits("push.credentials_validated")
        self.assertEqual((entry.data, entry.actor), ({"provider": "apns", "ok": True}, self.admin))
        self.validate_mock.return_value = {"ok": False, "detail": "Missing key."}
        self.assertEqual(self.post("/push/config/validate/fcm").json()["ok"], False)
        self.assertEqual(len(self.audits("push.credentials_validated")), 2)

    def test_validate_unknown_provider_and_crash(self):
        self.error(self.post("/push/config/validate/sms"), 404, "not_found")
        self.validate_mock.side_effect = RuntimeError("boom")
        r = self.post("/push/config/validate/webpush")
        self.assertEqual((r.status_code, r.json()["ok"]), (200, False))
        self.assertIn("boom", r.json()["detail"])


# --- sandbox ----------------------------------------------------------------------------------------------------------


class SandboxTests(PushBase):
    def test_get_shape(self):
        body = self.get("/push/sandbox").json()
        self.assertEqual(set(body), {"state", "scenarios", "events"})
        self.assertEqual(set(body["state"]), {"enabled", "effective", "expires_at", "scenario", "latency_ms", "default_from_settings"})
        self.assertEqual((body["state"]["enabled"], body["state"]["scenario"]), (None, "ok"))
        values = [s["value"] for s in body["scenarios"]]
        self.assertEqual(values, ["ok", "throttle", "error", "timeout"])  # 'dead' only in DEBUG
        self.assertTrue(all(s["label"] and s["description"] for s in body["scenarios"]))

    @override_settings(DEBUG=True)
    def test_dead_listed_in_debug(self):
        self.assertIn("dead", [s["value"] for s in self.get("/push/sandbox").json()["scenarios"]])

    def test_turning_on_needs_confirm(self):
        self.error(self.put("/push/sandbox/mode", {"enabled": True}), 422, "validation_error")
        self.error(self.put("/push/sandbox/mode", {"enabled": True, "confirm": False}), 422, "validation_error")
        self.assertFalse(SandboxState.objects.exists())
        self.assertFalse(self.audits("push.sandbox_mode"))

    def test_on_with_expiry_then_off(self):
        for minutes in (30, 60, 240, 1440, None):
            r = self.put("/push/sandbox/mode", {"enabled": True, "confirm": True, "expires_in_minutes": minutes})
            self.assertEqual(r.status_code, 200, r.content)
            state = r.json()["state"]
            self.assertEqual((state["enabled"], state["effective"]), (True, True))
            if minutes:
                expires = datetime.datetime.fromisoformat(state["expires_at"])
                self.assertAlmostEqual((expires - timezone.now()).total_seconds(), minutes * 60, delta=30)
            else:
                self.assertIsNone(state["expires_at"])
        off = self.put("/push/sandbox/mode", {"enabled": False}).json()
        self.assertEqual((off["state"]["enabled"], off["state"]["effective"], off["state"]["expires_at"]), (False, False, None))
        self.assertEqual(len(self.audits("push.sandbox_mode")), 6)
        self.assertEqual(self.audits("push.sandbox_mode")[0].data, {"enabled": True, "expires_in_minutes": 30})
        self.assertTrue(SandboxEvent.objects.exists())
        self.assertTrue(off["events"])
        self.assertEqual(set(off["events"][0]), {"id", "created", "user_email", "action", "detail"})
        self.assertEqual(off["events"][0]["user_email"], "admin@example.com")

    def test_bad_expiry_is_rejected(self):
        for bad in (45, 0, -1, "soon"):
            self.error(self.put("/push/sandbox/mode", {"enabled": True, "confirm": True, "expires_in_minutes": bad}), 422, "validation_error")
        self.assertFalse(SandboxState.objects.exists())

    def test_scenario_requires_sandbox_on(self):
        err = self.error(self.put("/push/sandbox/scenario", {"scenario": "throttle", "latency_ms": 100}), 409, "conflict")
        self.assertEqual(err["message"], "Switch the sandbox on first.")
        self.assertFalse(self.audits("push.sandbox_scenario"))

    def test_scenario_change(self):
        self.put("/push/sandbox/mode", {"enabled": True, "confirm": True})
        r = self.put("/push/sandbox/scenario", {"scenario": "throttle", "latency_ms": 250})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["state"]["scenario"], r.json()["state"]["latency_ms"]), ("throttle", 250))
        (entry,) = self.audits("push.sandbox_scenario")
        self.assertEqual(entry.data, {"scenario": "throttle", "latency_ms": 250})

    def test_scenario_validation(self):
        self.put("/push/sandbox/mode", {"enabled": True, "confirm": True})
        for body in ({"scenario": "nope", "latency_ms": 0}, {"scenario": "ok", "latency_ms": -1}, {"scenario": "ok", "latency_ms": 10001}, {"scenario": "dead", "latency_ms": 0}):
            self.error(self.put("/push/sandbox/scenario", body), 422, "validation_error")
        self.assertEqual(self.put("/push/sandbox/scenario", {"scenario": "ok", "latency_ms": 10000}).status_code, 200)
        self.assertEqual(self.put("/push/sandbox/scenario", {"scenario": "ok", "latency_ms": 0}).status_code, 200)

    @override_settings(DEBUG=True)
    def test_dead_scenario_allowed_in_debug(self):
        self.put("/push/sandbox/mode", {"enabled": True, "confirm": True})
        r = self.put("/push/sandbox/scenario", {"scenario": "dead", "latency_ms": 0})
        self.assertEqual((r.status_code, r.json()["state"]["scenario"]), (200, "dead"))

    def msg(self, **kw):
        return SandboxMessage.objects.create(**{"channel": "native", "provider": "apns", "target": "inst-1", "title": "T", "body": "B", "request": {"aps": {}}, "outcome": "delivered", **kw})

    def test_messages_shape_labels_and_filters(self):
        user = make_user("m@club.test")
        club = create_club("Msg FC", self.admin)
        PushDevice.objects.create(user=user, installation_id="inst-1", platform="ios", environment="production", app_version="2.3.4", push_token="t")
        PushDevice.objects.create(user=user, installation_id="inst-off", platform="android", push_token="t2", enabled=False)
        self.msg(user=user, club=club)
        self.msg(target="gone", outcome="error", provider="fcm")
        self.msg(target="inst-off", provider="fcm", outcome="throttled")
        self.msg(channel="web", provider="webpush", target="https://push.example/abc", outcome="delivered")
        body = self.get("/push/sandbox/messages").json()
        self.assertEqual(len(body["items"]), 4)
        self.assertEqual(body["outcomes"], {"delivered": 2, "error": 1, "throttled": 1})
        self.assertEqual(body["providers"], ["apns", "fcm", "webpush"])
        labels = {i["target"]: i["target_label"] for i in body["items"]}
        self.assertEqual(labels["inst-1"], "iOS · production · v2.3.4 · enabled")
        self.assertEqual(labels["gone"], "device removed")
        self.assertEqual(labels["inst-off"], "Android · production · disabled")
        first = next(i for i in body["items"] if i["target"] == "inst-1")
        self.assertEqual(set(first), {"id", "created", "channel", "provider", "club", "user_email", "target", "target_label", "title", "body", "request", "outcome"})
        self.assertEqual((first["club"], first["user_email"], first["request"]), ({"id": str(club.pk), "name": "Msg FC"}, "m@club.test", {"aps": {}}))
        ids = lambda q: {i["target"] for i in self.get("/push/sandbox/messages" + q).json()["items"]}  # noqa: E731
        self.assertEqual(ids("?provider=fcm"), {"gone", "inst-off"})
        self.assertEqual(ids("?channel=web"), {"https://push.example/abc"})
        self.assertEqual(ids("?outcome=error"), {"gone"})
        self.assertEqual(ids(f"?club={club.pk}"), {"inst-1"})
        self.assertEqual(ids("?club=garbage"), {"inst-1", "gone", "inst-off", "https://push.example/abc"})
        self.assertEqual(ids("?provider=fcm&outcome=throttled"), {"inst-off"})
        self.assertEqual(self.get("/push/sandbox/messages?provider=fcm").json()["outcomes"], {"error": 1, "throttled": 1})

    def test_messages_capped_at_100_newest_first(self):
        SandboxMessage.objects.bulk_create([SandboxMessage(channel="native", provider="apns", target=f"t{i}", outcome="delivered") for i in range(120)])
        newest = SandboxMessage.objects.create(channel="native", provider="apns", target="newest", outcome="delivered")
        items = self.get("/push/sandbox/messages").json()["items"]
        self.assertEqual(len(items), 100)
        self.assertEqual(items[0]["id"], str(newest.pk))
        self.assertEqual(self.get("/push/sandbox/messages").json()["outcomes"], {"delivered": 121})

    def test_clear(self):
        self.msg()
        self.msg()
        r = self.delete("/push/sandbox/messages")
        self.assertEqual((r.status_code, r.json()), (200, {"deleted": 2}))
        self.assertFalse(SandboxMessage.objects.exists())
        (entry,) = self.audits("push.sandbox_cleared")
        self.assertEqual(entry.data, {"deleted": 2})
        self.assertEqual(self.delete("/push/sandbox/messages").json(), {"deleted": 0})


# --- composer ---------------------------------------------------------------------------------------------------------


class ComposerTests(PushBase):
    def setUp(self):
        super().setUp()
        self.user = make_user("target@club.test", first="Tara", last="Target")
        self.device = PushDevice.objects.create(user=self.user, installation_id="inst-composer", platform="ios", push_token="tok")
        self.sub = WebPushSubscription.objects.create(user=self.user, endpoint="https://push.example/sub", p256dh="p", auth="a")

    def body(self, **kw):
        return {"target": {"type": "user", "id": str(self.user.pk)}, "title": "Hello", "body": "World", "mode": "sandbox", **kw}

    def test_sandbox_mode_forces_sandbox(self):
        self.send_mock.return_value = [result(sandboxed=True, target_masked="inst-comp…"), result(channel="web", provider="webpush", status="failed", error="boom", sandboxed=True)]
        r = self.post("/push/send", self.body(data={"a": 1}, category="event", priority="critical"))
        self.assertEqual(r.status_code, 200, r.content)
        out = r.json()
        self.assertEqual((out["mode"], out["sandboxed"]), ("sandbox", True))
        self.assertEqual(out["results"][1], {"channel": "web", "provider": "webpush", "target_masked": "tok", "status": "failed", "error": "boom", "duration_ms": 12})
        args, kwargs = self.send_mock.call_args
        self.assertEqual(args, (self.user,))
        self.assertEqual((kwargs["title"], kwargs["body"], kwargs["category"], kwargs["priority"], kwargs["data"]), ("Hello", "World", "event", "critical", {"a": 1}))
        self.assertEqual((kwargs["force_sandbox"], kwargs["respect_preferences"], kwargs["only"]), (True, False, None))

    def test_live_needs_confirm(self):
        for extra in ({}, {"confirm_live": False}):
            err = self.error(self.post("/push/send", self.body(mode="live", **extra)), 422, "validation_error")
            self.assertIn("confirm_live", err["fields"])
        self.send_mock.assert_not_called()
        self.assertFalse(self.audits("push.test_sent"))

    def test_live_with_confirm(self):
        r = self.post("/push/send", self.body(mode="live", confirm_live=True))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["mode"], r.json()["sandboxed"]), ("live", False))
        self.assertFalse(self.send_mock.call_args.kwargs["force_sandbox"])

    def test_live_while_global_sandbox_is_on_reports_sandboxed(self):
        self.send_mock.return_value = [result(sandboxed=True)]
        r = self.post("/push/send", self.body(mode="live", confirm_live=True))
        self.assertEqual((r.json()["mode"], r.json()["sandboxed"]), ("live", True))

    def test_single_device_and_web_subscription(self):
        self.post("/push/send", self.body(target={"type": "device", "id": str(self.device.pk)}))
        self.assertEqual(self.send_mock.call_args.kwargs["only"], self.device)
        self.assertEqual(self.send_mock.call_args.args, (self.user,))
        self.post("/push/send", self.body(target={"type": "web_subscription", "id": str(self.sub.pk)}))
        self.assertEqual(self.send_mock.call_args.kwargs["only"], self.sub)

    def test_unknown_targets_are_404(self):
        ghost = "00000000-0000-0000-0000-0000000000aa"
        for t in ("user", "device", "web_subscription"):
            self.error(self.post("/push/send", self.body(target={"type": t, "id": ghost})), 404, "not_found")
        self.send_mock.assert_not_called()

    def test_field_validation(self):
        bad = [
            {"title": ""}, {"title": "x" * 121}, {"title": "   "}, {"body": ""}, {"body": "x" * 501}, {"category": "nope"}, {"priority": "urgent"}, {"mode": "dry"},
            {"data": ["list"]}, {"data": {"blob": "x" * 2100}}, {"target": {"type": "team", "id": str(self.user.pk)}},
        ]  # fmt: skip
        for extra in bad:
            r = self.post("/push/send", self.body(**extra))
            self.assertEqual(r.status_code, 422, extra)
        self.send_mock.assert_not_called()
        ok = self.post("/push/send", self.body(title="x" * 120, body="y" * 500, data={"blob": "x" * 1900}))
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(self.post("/push/send", self.body(data=None)).status_code, 200)

    def test_data_limit_counts_bytes(self):
        self.assertEqual(self.post("/push/send", self.body(data={"k": "é" * 1100})).status_code, 422)  # 2200 bytes
        self.assertEqual(self.post("/push/send", self.body(data={"k": "é" * 900})).status_code, 200)

    def test_audit_has_target_mode_counts_and_title_but_not_body(self):
        self.send_mock.return_value = [result(), result(status="failed"), result()]
        self.post("/push/send", self.body(title="Visible title", body="Secret body text"))
        (entry,) = self.audits("push.test_sent")
        self.assertEqual(entry.actor, self.admin)
        self.assertEqual(entry.data["target"], {"type": "user", "id": str(self.user.pk)})
        self.assertEqual((entry.data["mode"], entry.data["title"], entry.data["counts"], entry.data["results"]), ("sandbox", "Visible title", {"sent": 2, "failed": 1}, 3))
        self.assertNotIn("Secret body text", str(entry.data))

    def test_real_engine_sandbox_send_captures_instead_of_sending(self):
        if real_engine("services") is None:
            self.skipTest("engine modules not present")
        self.stack.close()  # real send_now, real fake gateway: nothing leaves the process
        r = self.post("/push/send", self.body(target={"type": "device", "id": str(self.device.pk)}))
        self.assertEqual(r.status_code, 200, r.content)
        out = r.json()
        self.assertEqual((out["mode"], out["sandboxed"], len(out["results"])), ("sandbox", True, 1))
        self.assertEqual((out["results"][0]["provider"], out["results"][0]["status"]), ("apns", "sent"))
        self.assertEqual(SandboxMessage.objects.filter(target="inst-composer").count(), 1)

    def test_targets_search(self):
        make_user("zed@club.test", first="Zed", last="Zebra")
        for i in range(12):
            make_user(f"many{i}@bulk.test", first="Many", last=f"N{i}")
        body = self.get("/push/targets?q=tara").json()
        self.assertEqual(body, [{"type": "user", "id": str(self.user.pk), "label": "target@club.test (Tara Target)"}])
        self.assertEqual([t["label"].split(" ")[0] for t in self.get("/push/targets?q=zebra").json()], ["zed@club.test"])
        self.assertEqual(len(self.get("/push/targets?q=bulk.test").json()), 10)
        self.assertEqual(self.get("/push/targets?q=tara%20target").json()[0]["id"], str(self.user.pk))
        self.assertEqual(self.get("/push/targets?q=nobody-here").json(), [])
        self.assertEqual(self.get("/push/targets").json(), [])


# --- a user's push state ----------------------------------------------------------------------------------------------


class UserPushTests(PushBase):
    def setUp(self):
        super().setUp()
        self.user = make_user("owner@club.test")
        self.other = make_user("other@club.test")
        self.club = create_club("Push FC", self.admin)
        self.ios = PushDevice.objects.create(user=self.user, installation_id="ios-1", platform="ios", environment="sandbox", app_version="2.3.4", locale="nl-BE", push_token="t1")
        self.old = PushDevice.objects.create(user=self.user, installation_id="and-1", platform="android", push_token="t2", enabled=False, disabled_at=timezone.now())
        PushDevice.objects.filter(pk=self.old.pk).update(last_seen=timezone.now() - datetime.timedelta(days=90))
        self.sub = WebPushSubscription.objects.create(user=self.user, club=self.club, endpoint="https://push.example/" + "e" * 60, p256dh="p", auth="a", user_agent="Firefox")
        self.theirs = PushDevice.objects.create(user=self.other, installation_id="ios-2", platform="ios", push_token="t3")

    def test_following_lists_teams_and_games_per_club(self):
        from django_tenants.utils import schema_context

        from events.models import Event, EventFollow, GameFollow
        from teams.models import Team

        self.assertEqual(self.get(f"/users/{self.user.pk}/push/following").json(), {"clubs": [], "total_teams": 0, "total_games": 0})
        self.club.add_user(self.user) if hasattr(self.club, "add_user") else None
        with schema_context(self.club.schema_name):
            team = Team.objects.create(name="Sharks U16", short_name="U16")
            GameFollow.objects.create(user=self.user, team=team)
            game = Event.objects.create(title="Sharks vs Bears", kind="game", start=timezone.now())
            EventFollow.objects.create(user=self.user, event=game)
        body = self.get(f"/users/{self.user.pk}/push/following").json()
        self.assertEqual((body["total_teams"], body["total_games"]), (1, 1))
        self.assertEqual((body["clubs"][0]["club"]["name"], body["clubs"][0]["teams"][0]["name"], body["clubs"][0]["games"][0]["title"]), ("Push FC", "Sharks U16", "Sharks vs Bears"))

    def test_shape(self):
        QuietHours.objects.create(user=self.user, start=datetime.time(22, 0), end=datetime.time(7, 0), timezone="Europe/Brussels")
        UserNotificationSettings.objects.create(user=self.user, email_mode="unless_app")
        MasterSwitch.objects.create(user=self.user, club=None, channel="push", enabled=False)
        NotificationPreference.objects.create(user=self.user, club=self.club, category="shop", channel="push", enabled=False)
        NotificationPreference.objects.create(user=self.user, club=None, category="news", channel="email", enabled=True)
        until = timezone.now() + datetime.timedelta(hours=2)
        self.quiet_mock.return_value = (True, until)
        for i in range(12):
            self.delivery(user=self.user, status=Status.FAILED, minutes_ago=i)
        r = self.get(f"/users/{self.user.pk}/push")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(set(body), {"devices", "web_subscriptions", "preferences", "recent_deliveries"})
        by_id = {d["id"]: d for d in body["devices"]}
        self.assertEqual(set(by_id), {str(self.ios.pk), str(self.old.pk)})
        d = by_id[str(self.ios.pk)]
        self.assertEqual(
            {k: d[k] for k in ("platform", "platform_label", "provider", "environment", "app_version", "locale", "enabled", "disabled_at", "stale")},
            {"platform": "ios", "platform_label": "iOS", "provider": "apns", "environment": "sandbox", "app_version": "2.3.4", "locale": "nl-BE", "enabled": True, "disabled_at": None, "stale": False},
        )
        self.assertEqual((by_id[str(self.old.pk)]["stale"], by_id[str(self.old.pk)]["provider"], by_id[str(self.old.pk)]["enabled"]), (True, "fcm", False))
        self.assertEqual(set(d), {"id", "platform", "platform_label", "provider", "environment", "app_version", "locale", "enabled", "disabled_at", "last_seen", "stale"})
        (sub,) = body["web_subscriptions"]
        self.assertEqual((sub["club"], sub["user_agent"], len(sub["endpoint_masked"]) <= 24), ({"id": str(self.club.pk), "name": "Push FC"}, "Firefox", True))
        p = body["preferences"]
        self.assertEqual((p["email_mode"], p["email_mode_label"]), ("unless_app", "Only email me when the app can't notify me"))
        q = p["quiet_hours"]
        self.assertEqual((q["enabled"], q["start"], q["end"], q["timezone"], q["active_now"]), (True, "22:00", "07:00", "Europe/Brussels", True))
        self.assertIsNotNone(q["until"])
        self.assertEqual(p["master_switches"], [{"club": None, "channel": "push", "enabled": False}])
        self.assertEqual(p["categories_off"], [{"club": {"id": str(self.club.pk), "name": "Push FC"}, "category": "shop", "category_label": "Shop", "channel": "push"}])
        self.assertEqual(len(body["recent_deliveries"]), 10)
        self.assertEqual(set(body["recent_deliveries"][0]), {"id", "created", "provider", "status", "category", "title", "attempts", "error"})

    def test_defaults_for_a_user_without_anything(self):
        body = self.get(f"/users/{make_user('blank@club.test').pk}/push").json()
        self.assertEqual((body["devices"], body["web_subscriptions"], body["recent_deliveries"]), ([], [], []))
        self.assertEqual(body["preferences"], {"email_mode": "always", "email_mode_label": "Always send email", "quiet_hours": None, "master_switches": [], "categories_off": []})

    def test_unknown_user_404(self):
        self.error(self.get("/users/00000000-0000-0000-0000-0000000000aa/push"), 404, "not_found")

    def test_query_budget(self):
        with CaptureQueriesContext(connection) as ctx:
            self.get(f"/users/{self.user.pk}/push")
        self.assertLess(len(queries(ctx)), 40, [q[:100] for q in queries(ctx)])

    def test_remove_device_audited(self):
        r = self.delete(f"/users/{self.user.pk}/push/devices/{self.ios.pk}")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(PushDevice.objects.filter(pk=self.ios.pk).exists())
        self.assertTrue(PushDevice.objects.filter(pk=self.theirs.pk).exists())
        (entry,) = self.audits("push.device_removed")
        self.assertEqual((entry.target_type, entry.target_id, entry.actor), ("user", str(self.user.pk), self.admin))
        self.assertEqual((entry.data["device_id"], entry.data["platform"]), (str(self.ios.pk), "ios"))
        self.assertNotIn("t1", entry.data.values())

    def test_remove_device_of_another_user_is_404_and_changes_nothing(self):
        self.error(self.delete(f"/users/{self.user.pk}/push/devices/{self.theirs.pk}"), 404, "not_found")
        self.error(self.delete(f"/users/{self.other.pk}/push/devices/{self.ios.pk}"), 404, "not_found")
        self.error(self.delete(f"/users/{self.user.pk}/push/devices/00000000-0000-0000-0000-0000000000aa"), 404, "not_found")
        self.assertEqual(PushDevice.objects.count(), 3)
        self.assertFalse(self.audits("push.device_removed"))

    def test_remove_web_subscription(self):
        self.error(self.delete(f"/users/{self.other.pk}/push/web-subscriptions/{self.sub.pk}"), 404, "not_found")
        self.assertTrue(WebPushSubscription.objects.exists())
        r = self.delete(f"/users/{self.user.pk}/push/web-subscriptions/{self.sub.pk}")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(WebPushSubscription.objects.exists())
        (entry,) = self.audits("push.web_subscription_removed")
        self.assertEqual((entry.target_id, entry.data["subscription_id"]), (str(self.user.pk), str(self.sub.pk)))


class SandboxBannerSwitchTests(UserApiBase):
    """The platform-wide banner reads the sandbox state from /switches, for staff as well as superusers."""

    def test_switches_report_the_sandbox(self):
        r = self.get("/switches")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["push_sandbox"], {"is_active": False, "expires_at": None})
        from notifications import sandbox

        sandbox.set_mode(self.admin, True, 60)
        body = self.get("/switches").json()["push_sandbox"]
        self.assertTrue(body["is_active"])
        self.assertIsNotNone(body["expires_at"])


class InvoicingBannerSwitchTests(UserApiBase):
    """The platform-wide banner reads whether automatic club invoicing is off from /switches."""

    def test_switches_report_manual_invoicing(self):
        self.assertEqual(self.get("/switches").json()["manual_invoicing"], {"is_active": False, "pending_count": 0})  # automatic by default
        from billing.models import BillingSettings

        settings = BillingSettings.current()
        settings.auto_invoicing = False
        settings.save()
        body = self.get("/switches").json()["manual_invoicing"]
        self.assertTrue(body["is_active"])
        self.assertIsInstance(body["pending_count"], int)
