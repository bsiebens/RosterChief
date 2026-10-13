from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from appapi.models import PushDevice
from club.models import Club, ClubMembership, Season
from members.models import Member
from notifications import sandbox
from notifications.models import Notification, SandboxEvent, SandboxMessage
from rosterchief.test_support import enrol_mfa

from .tests import ControlPanelTestBase, User

Out = SandboxMessage.Outcome


class SandboxTestBase(ControlPanelTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.root = User.objects.create_superuser(email="super@example.com", password="pw-secret-123")
        enrol_mfa(cls.root)

    def setUp(self):
        super().setUp()
        self.client.force_login(self.root)
        self.url = reverse("controlpanel:push_sandbox")

    def msg(self, **kw):
        defaults = {"channel": "native", "provider": "apns", "target": "tok", "title": "Hi", "body": "body", "outcome": Out.DELIVERED, "club": self.club, "request": {"aps": {"alert": "x"}}}
        return SandboxMessage.objects.create(**{**defaults, **kw})


class AccessTests(SandboxTestBase):
    def test_access(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(User.objects.create_user(email="member@example.com", password="pw-secret-123"))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {"scenario": "ok"}).status_code, 403)

    @override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=[".rosterchief.app"])
    def test_club_subdomain_404(self):
        Club.objects.create(name="Rival FC", slug="rival-fc")
        response = self.client.get(self.url, headers={"host": "rival-fc.rosterchief.app"})
        self.assertEqual(response.status_code, 404)


class LiveTests(SandboxTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_live_state_has_switch_and_disabled_scenario(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'data-testid="mode-live"')
        self.assertContains(response, "Switch to sandbox")
        self.assertContains(response, "The scenario only applies while the sandbox is on")
        self.assertContains(response, "disabled")
        self.assertNotContains(response, "sandbox-banner-global")

    def test_scenario_and_sample_refused_while_live(self):
        self.assertEqual(self.client.post(self.url, {"action": "set_scenario", "scenario": "throttle"}).status_code, 403)
        self.assertEqual(sandbox.scenario()["name"], "ok")
        self.assertEqual(self.client.post(reverse("controlpanel:push_sandbox_sample")).status_code, 403)

    def test_switch_on_requires_confirmation(self):
        self.client.post(self.url, {"action": "sandbox_on", "expires_in": "60"})
        self.assertFalse(sandbox.enabled())
        self.assertFalse(SandboxEvent.objects.exists())

    def test_switch_on_records_event_and_expiry(self):
        self.client.post(self.url, {"action": "sandbox_on", "confirm": "1", "expires_in": "240"})
        self.assertTrue(sandbox.enabled())
        status = sandbox.status()
        self.assertAlmostEqual((status["expires_at"] - timezone.now()).total_seconds(), 4 * 3600, delta=60)
        event = SandboxEvent.objects.get()
        self.assertEqual((event.user, event.action), (self.root, "sandbox_on"))

    def test_never_expires(self):
        self.client.post(self.url, {"action": "sandbox_on", "confirm": "1", "expires_in": "0"})
        self.assertIsNone(sandbox.status()["expires_at"])

    def test_invalid_expiry(self):
        self.client.post(self.url, {"action": "sandbox_on", "confirm": "1", "expires_in": "7"})
        self.assertFalse(sandbox.enabled())

    def test_switch_back_to_live_needs_no_confirmation(self):
        sandbox.set_mode(self.root, on=True)
        self.client.post(self.url, {"action": "live"})
        self.assertFalse(sandbox.enabled())
        self.assertEqual(list(SandboxEvent.objects.values_list("action", flat=True)), ["live", "sandbox_on"])

    def test_banner_on_other_pages_only_while_on(self):
        dashboard = reverse("controlpanel:dashboard")
        self.assertNotContains(self.client.get(dashboard), "Sandbox mode: nothing is sent to users")
        sandbox.set_mode(self.root, on=True)
        self.assertContains(self.client.get(dashboard), "Sandbox mode: nothing is sent to users")
        self.assertContains(self.client.get(reverse("controlpanel:push_monitoring")), "push-sandbox-badge")

    def test_audit_trail_renders(self):
        sandbox.set_mode(self.root, on=True, expires_in=timedelta(hours=1))
        response = self.client.get(self.url)
        self.assertContains(response, "super@example.com")
        self.assertContains(response, "sandbox_on")
        self.assertContains(response, "Switches back to live at")


class OnTests(SandboxTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        sandbox.set_mode(self.root, on=True)

    def test_renders(self):
        self.msg(title="Special title")
        response = self.client.get(self.url)
        self.assertContains(response, "Sandbox mode: nothing is sent to users")
        self.assertContains(response, 'name="scenario"')
        self.assertContains(response, "Special title")
        self.assertContains(response, "Send a sample push to myself")

    def test_scenario_persists(self):
        response = self.client.post(self.url, {"action": "set_scenario", "scenario": "throttle", "latency_ms": "250"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(sandbox.scenario(), {"name": "throttle", "latency_ms": 250})
        self.assertContains(self.client.get(self.url), "Current: Throttle (429), latency 250 ms")

    def test_invalid_rejected(self):
        sandbox.set_scenario("dead", 5)
        for data in ({"scenario": "bogus", "latency_ms": "0"}, {"scenario": "ok", "latency_ms": "10001"}, {"scenario": "ok", "latency_ms": "-1"}, {"scenario": "ok", "latency_ms": "abc"}):
            self.client.post(self.url, {"action": "set_scenario", **data})
            self.assertEqual(sandbox.scenario(), {"name": "dead", "latency_ms": 5})

    def test_filters_and_counts(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        self.msg(title="a-apns", provider="apns")
        self.msg(title="b-fcm", provider="fcm", channel="native", outcome=Out.THROTTLED)
        self.msg(title="c-mail", provider="smtp", channel="email", club=other)
        r = self.client.get(self.url, {"provider": "fcm"})
        self.assertEqual([m.title for m in r.context["captured"]], ["b-fcm"])
        r = self.client.get(self.url, {"outcome": "throttled"})
        self.assertEqual([m.title for m in r.context["captured"]], ["b-fcm"])
        self.assertEqual({v: n for v, _l, n in r.context["outcome_counts"]}["throttled"], 1)
        r = self.client.get(self.url, {"channel": "email"})
        self.assertEqual([m.title for m in r.context["captured"]], ["c-mail"])
        r = self.client.get(self.url, {"club": str(other.pk)})
        self.assertEqual([m.title for m in r.context["captured"]], ["c-mail"])
        r = self.client.get(self.url, {"club": "not-a-uuid", "outcome": "nonsense"})
        self.assertEqual(len(r.context["captured"]), 3)

    def test_limit_100(self):
        SandboxMessage.objects.bulk_create([SandboxMessage(channel="native", provider="apns", target="t") for _i in range(105)])
        self.assertEqual(len(self.client.get(self.url).context["captured"]), 100)

    def test_clear_all(self):
        self.msg()
        self.msg()
        self.assertEqual(self.client.post(reverse("controlpanel:push_sandbox_clear")).status_code, 302)
        self.assertEqual(SandboxMessage.objects.count(), 0)

    def test_sample_without_club(self):
        response = self.client.post(reverse("controlpanel:push_sandbox_sample"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Notification.objects.count(), 0)

    def test_sample_push(self):
        member = Member.objects.create(user=self.root, first_name="Su", last_name="Per", email=self.root.email)
        season = Season.objects.create(club=self.club, start_date="2026-01-01", end_date="2026-12-31")
        ClubMembership.objects.create(club=self.club, member=member, season=season)
        PushDevice.objects.create(user=self.root, installation_id="i1", platform="ios", push_token="tok")
        self.client.post(reverse("controlpanel:push_sandbox_sample"))
        self.assertEqual(Notification.objects.filter(member=member, club=self.club).count(), 1)
