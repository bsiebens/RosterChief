import datetime
import io
import json
from pathlib import Path

from allauth.account.models import EmailAddress
from allauth.headless.tokens.strategies.jwt import internal as jwt_internal
from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
from botocore.stub import Stubber
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from authentication.test_headless_app import _current_totp_code
from club.models import Club, ClubMembership, ClubRole, Season
from features.models import Maintenance
from members.models import Family, FamilyMembership, Member
from rosterchief.test_support import make_club
from teams.models import Position, StaffAssignment, Team

from .errors import ApiError
from .idempotency import idempotent
from .management.commands.export_app_openapi import render_schema
from .models import IdempotencyRecord, PushDevice
from .pagination import paginate
from .push import NullGateway, SnsGateway, build_message

User = get_user_model()

BASE = "rosterchief.app"
CLUB_HOST = "ajax-united.rosterchief.app"
OTHER_HOST = "rival-fc.rosterchief.app"
HOSTS = {"ROSTERCHIEF_BASE_DOMAIN": BASE, "ALLOWED_HOSTS": [BASE, CLUB_HOST, OTHER_HOST, "testserver"]}
PASSWORD = "correct horse battery staple"
SNAPSHOT = Path(settings.BASE_DIR) / "docs" / "app-api" / "openapi-app-v1.json"


class RecordingGateway(NullGateway):
    """Test double: every install "gets" an endpoint; calls are recorded."""

    calls: list = []

    def register(self, device):
        RecordingGateway.calls.append(("register", device.installation_id))
        return f"arn:aws:sns:eu-west-1:1:endpoint/APNS/app/{device.installation_id}"

    def unregister(self, device):
        RecordingGateway.calls.append(("unregister", device.installation_id))


def current_season(club):
    today = timezone.localdate()
    return Season.objects.create(club=club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))


@override_settings(**HOSTS)
class AppApiTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.club = make_club()
        self.season = current_season(self.club)
        self.user = User.objects.create_user(email="parent@example.com", password=PASSWORD)
        EmailAddress.objects.create(user=self.user, email=self.user.email, verified=True, primary=True)
        self.member = Member.objects.create(user=self.user, first_name="Pat", last_name="Parent")
        ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, kind=ClubMembership.Kind.GUARDIAN, status=ClubMembership.StatusChoices.ACTIVE)
        self.child = Member.objects.create(first_name="Kim", last_name="Parent", date_of_birth=datetime.date(2014, 5, 1))
        ClubMembership.objects.create(club=self.club, member=self.child, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        family = Family.objects.create(club=self.club)
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=self.child, role=FamilyMembership.FamilyRole.CHILD)

    def login(self, email=None):
        response = self.client.post("/api/auth/app/v1/auth/login", {"email": email or self.user.email, "password": PASSWORD}, content_type="application/json", HTTP_HOST=BASE)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["meta"]["access_token"]

    def api(self, method, path, token=None, host=BASE, data=None, **headers):
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        kwargs = {"HTTP_HOST": host, **headers}
        if data is not None:
            kwargs.update(data=json.dumps(data), content_type="application/json")
        return getattr(self.client, method)(f"/api/app/v1{path}", **kwargs)

    def login_with_totp(self):
        secret = generate_totp_secret()
        TOTP.activate(self.user, secret)
        first = self.client.post("/api/auth/app/v1/auth/login", {"email": self.user.email, "password": PASSWORD}, content_type="application/json", HTTP_HOST=BASE)
        second = self.client.post("/api/auth/app/v1/auth/2fa/authenticate", {"code": _current_totp_code(secret)}, content_type="application/json", HTTP_HOST=BASE, HTTP_X_SESSION_TOKEN=first.json()["meta"]["session_token"])
        self.assertEqual(second.status_code, 200, second.content)
        return second.json()["meta"]["access_token"]

    def assertError(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertTrue(response.json()["error"]["message"])


class AuthTests(AppApiTestCase):
    def test_no_token_is_unauthenticated(self):
        response = self.api("get", "/me")
        self.assertError(response, 401, "unauthenticated")
        self.assertEqual(response["WWW-Authenticate"], "Bearer")

    def test_garbage_token_is_unauthenticated(self):
        self.assertError(self.api("get", "/me", token="not-a-jwt"), 401, "unauthenticated")

    def test_expired_token_says_so(self):
        self.login()
        session = Session.objects.get()
        sid = jwt_internal.session_key_to_sid(session.session_key)
        expired, _payload = jwt_internal.create_token("access", sub=str(self.user.pk), sid=sid, expires_in=-60)

        self.assertError(self.api("get", "/me", token=expired), 401, "token_expired")

    def test_signed_out_token_stops_working(self):
        token = self.login()
        self.client.delete("/api/auth/app/v1/auth/session", HTTP_HOST=BASE, HTTP_AUTHORIZATION=f"Bearer {token}")

        self.assertError(self.api("get", "/me", token=token), 401, "unauthenticated")

    def test_inactive_account_is_refused(self):
        token = self.login()
        User.objects.filter(pk=self.user.pk).update(is_active=False)

        self.assertError(self.api("get", "/me", token=token), 401, "unauthenticated")

    def test_privileged_user_without_mfa_must_enrol(self):
        token = self.login()
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": ClubRole.Roles.ADMIN})

        self.assertError(self.api("get", "/me", token=token), 403, "mfa_enrollment_required")
        self.assertError(self.api("get", "/context", token=token, host=CLUB_HOST), 403, "mfa_enrollment_required")

    def test_privileged_user_with_mfa_gets_through(self):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": ClubRole.Roles.ADMIN})
        token = self.login_with_totp()

        response = self.api("get", "/me", token=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mfa"], {"enabled": True, "types": ["totp"]})


class AccountPlaneTests(AppApiTestCase):
    def test_meta_needs_no_token(self):
        response = self.api("get", "/meta")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["api_version"], "1")
        self.assertEqual(body["min_supported_app_version"]["ios"], settings.APP_MIN_VERSION_IOS)
        self.assertTrue(body["docs_url"].endswith("/api/docs/"))

    def test_account_endpoints_do_not_exist_on_a_club_host(self):
        token = self.login()
        self.assertError(self.api("get", "/meta", host=CLUB_HOST), 404, "not_found")
        self.assertError(self.api("get", "/me", token=token, host=CLUB_HOST), 404, "not_found")
        self.assertError(self.api("get", "/clubs", token=token, host=CLUB_HOST), 404, "not_found")

    def test_me(self):
        body = self.api("get", "/me", token=self.login()).json()

        self.assertEqual(body["user"], {"id": str(self.user.pk), "email": self.user.email})
        self.assertEqual(body["member"], {"id": str(self.member.pk), "first_name": "Pat", "last_name": "Parent"})
        self.assertEqual(body["mfa"], {"enabled": False, "types": []})

    def test_clubs_lists_tied_clubs_only(self):
        rival = make_club(name="Rival FC", slug="rival-fc")
        archived = make_club(name="Gone", slug="gone", archived_at=timezone.now())
        ClubMembership.objects.create(club=archived, member=self.member, season=current_season(archived))
        cancelled = make_club(name="Former", slug="former")
        ClubMembership.objects.create(club=cancelled, member=self.member, season=current_season(cancelled), status=ClubMembership.StatusChoices.CANCELLED)

        items = self.api("get", "/clubs", token=self.login()).json()["items"]

        self.assertEqual([item["slug"] for item in items], ["ajax-united"])
        item = items[0]
        self.assertEqual(item["api_base_url"], f"http://{CLUB_HOST}/api/app/v1")
        self.assertEqual(item["relationship"], ["guardian"])
        self.assertEqual(item["features"], {"shop": False, "shop_open": False, "forms": False, "officials": False, "evaluations": False})
        self.assertNotIn(str(rival.pk), [entry["id"] for entry in items])

    def test_clubs_reports_coach_and_member(self):
        ClubMembership.objects.filter(member=self.member).update(kind=ClubMembership.Kind.MEMBER)
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        position = Position.objects.create(club=self.club, name="Assistant", short_name="AC", staff_position=True)
        StaffAssignment.objects.create(team=team, member=self.member, season=self.season, position=position)

        item = self.api("get", "/clubs", token=self.login()).json()["items"][0]

        self.assertEqual(item["relationship"], ["member", "guardian", "coach"])

    def test_clubs_reports_board_roles_as_staff(self):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": ClubRole.Roles.EDITOR})
        token = self.login_with_totp()

        self.assertIn("staff", self.api("get", "/clubs", token=token).json()["items"][0]["relationship"])

    def test_clubs_features_follow_the_club_flags(self):
        from waffle import get_waffle_flag_model

        flag = get_waffle_flag_model().objects.create(name="shop", everyone=None)
        flag.clubs.add(self.club)
        Club.objects.filter(pk=self.club.pk).update(shop_open=True)

        features = self.api("get", "/clubs", token=self.login()).json()["items"][0]["features"]

        self.assertTrue(features["shop"])
        self.assertTrue(features["shop_open"])


class ContextTests(AppApiTestCase):
    def test_context_for_a_parent(self):
        body = self.api("get", "/context", token=self.login(), host=CLUB_HOST).json()

        self.assertEqual(body["club"]["slug"], "ajax-united")
        self.assertEqual(body["season"]["id"], str(self.season.pk))
        self.assertEqual(body["me"]["id"], str(self.member.pk))
        self.assertEqual([(person["first_name"], person["is_self"]) for person in body["managed_people"]], [("Pat", True), ("Kim", False)])
        self.assertEqual(body["managed_people"][1]["date_of_birth"], "2014-05-01")
        self.assertEqual(body["staffed_teams"], [])
        self.assertEqual(body["permissions"], {"has_management_access": False, "can_add_news": False, "can_manage_evaluations": False})
        self.assertEqual(body["unread_notifications"], 0)

    def test_context_lists_staffed_teams(self):
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        position = Position.objects.create(club=self.club, name="Assistant", short_name="AC", staff_position=True)
        StaffAssignment.objects.create(team=team, member=self.member, season=self.season, position=position)

        body = self.api("get", "/context", token=self.login(), host=CLUB_HOST).json()

        self.assertEqual(body["staffed_teams"], [{"id": str(team.pk), "name": "U16", "short_name": "U16"}])

    def test_other_club_is_not_a_member(self):
        make_club(name="Rival FC", slug="rival-fc")
        self.assertError(self.api("get", "/context", token=self.login(), host=OTHER_HOST), 403, "not_a_member")

    def test_base_domain_has_no_club(self):
        self.assertError(self.api("get", "/context", token=self.login()), 404, "not_found")

    def test_a_child_from_another_clubs_family_is_not_managed_here(self):
        rival = make_club(name="Rival FC", slug="rival-fc")
        ClubMembership.objects.create(club=rival, member=self.member, season=current_season(rival))

        body = self.api("get", "/context", token=self.login(), host=OTHER_HOST).json()

        self.assertEqual([person["first_name"] for person in body["managed_people"]], ["Pat"])


@override_settings(APP_PUSH_GATEWAY="appapi.tests.RecordingGateway")
class DeviceTests(AppApiTestCase):
    def setUp(self):
        super().setUp()
        RecordingGateway.calls = []

    def device(self, **overrides):
        return {"installation_id": "4b7c1e0a-9f1b-4c1e-8f7a-1234567890ab", "platform": "ios", "push_token": "a1b2c3", "app_version": "1.0.0", "locale": "nl", **overrides}

    def test_register_and_update(self):
        token = self.login()
        first = self.api("post", "/devices", token=token, data=self.device())

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json(), {"installation_id": self.device()["installation_id"], "platform": "ios", "environment": "production", "push_enabled": True})
        self.api("post", "/devices", token=token, data=self.device(push_token="d4e5f6", environment="sandbox"))

        device = PushDevice.objects.get()
        self.assertEqual((device.push_token, device.environment, device.user), ("d4e5f6", "sandbox", self.user))
        self.assertEqual([call for call, _id in RecordingGateway.calls], ["register", "register"])

    def test_same_token_again_does_not_touch_sns(self):
        token = self.login()
        self.api("post", "/devices", token=token, data=self.device())
        self.api("post", "/devices", token=token, data=self.device(app_version="1.0.1"))

        self.assertEqual(len(RecordingGateway.calls), 1)
        self.assertEqual(PushDevice.objects.get().app_version, "1.0.1")

    def test_install_moves_to_the_account_that_signs_in(self):
        other = User.objects.create_user(email="other@example.com", password=PASSWORD)
        EmailAddress.objects.create(user=other, email=other.email, verified=True, primary=True)
        self.api("post", "/devices", token=self.login(), data=self.device())

        self.api("post", "/devices", token=self.login("other@example.com"), data=self.device())

        self.assertEqual(PushDevice.objects.get().user, other)

    def test_invalid_payload_lists_fields(self):
        response = self.api("post", "/devices", token=self.login(), data=self.device(platform="windows", push_token=""))

        self.assertError(response, 422, "validation_error")
        self.assertEqual(set(response.json()["error"]["fields"]), {"payload.platform", "payload.push_token"})

    def test_unregister_own_device_only(self):
        other = User.objects.create_user(email="other@example.com", password=PASSWORD)
        PushDevice.objects.create(user=other, installation_id="someone-elses-install", platform="ios", push_token="x")
        token = self.login()
        self.api("post", "/devices", token=token, data=self.device())

        self.assertError(self.api("delete", "/devices/someone-elses-install", token=token), 404, "not_found")
        self.assertEqual(self.api("delete", f"/devices/{self.device()['installation_id']}", token=token).status_code, 204)
        self.assertEqual(list(PushDevice.objects.values_list("installation_id", flat=True)), ["someone-elses-install"])

    @override_settings(APP_PUSH_GATEWAY="")
    def test_without_sns_registration_works_but_push_is_off(self):
        response = self.api("post", "/devices", token=self.login(), data=self.device())

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["push_enabled"])


class CrossCuttingTests(AppApiTestCase):
    def test_responses_are_not_cacheable(self):
        response = self.api("get", "/me", token=self.login())
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_no_cors_headers(self):
        response = self.api("get", "/meta", HTTP_ORIGIN="https://evil.example")
        self.assertFalse([header for header in response.headers if header.lower().startswith("access-control-")])

    def test_maintenance_answers_in_the_envelope(self):
        token = self.login()
        Maintenance.start(message="Back at 10.")
        self.addCleanup(cache.clear)

        response = self.api("get", "/context", token=token, host=CLUB_HOST)

        self.assertError(response, 503, "maintenance")
        self.assertEqual(response.json()["error"]["message"], "Back at 10.")
        self.assertError(self.api("get", "/meta"), 503, "maintenance")

    def test_unknown_route_is_an_enveloped_404(self):
        self.assertEqual(self.api("get", "/no-such-thing").status_code, 404)


class IdempotencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="parent@example.com", password=PASSWORD)
        self.factory = RequestFactory()
        self.runs = 0

    def request(self, key="k-1", path="/api/app/v1/shop/orders"):
        request = self.factory.post(path, HTTP_IDEMPOTENCY_KEY=key) if key else self.factory.post(path)
        request.user = self.user
        return request

    def work(self, status=201):
        def run():
            self.runs += 1
            return status, {"order": self.runs}

        return run

    def test_repeat_replays_the_first_response(self):
        self.assertEqual(idempotent(self.request(), self.work()), (201, {"order": 1}))
        self.assertEqual(idempotent(self.request(), self.work()), (201, {"order": 1}))
        self.assertEqual(self.runs, 1)

    def test_without_a_key_it_just_runs(self):
        idempotent(self.request(key=None), self.work())
        idempotent(self.request(key=None), self.work())
        self.assertEqual(self.runs, 2)

    def test_a_failed_attempt_can_be_retried(self):
        self.assertEqual(idempotent(self.request(), self.work(status=409))[0], 409)
        self.assertEqual(idempotent(self.request(), self.work())[0], 201)
        self.assertEqual(self.runs, 2)

    def test_an_exception_releases_the_key(self):
        def boom():
            raise ApiError(409, "shop_closed")

        with self.assertRaises(ApiError):
            idempotent(self.request(), boom)
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_in_progress_key_is_a_conflict(self):
        IdempotencyRecord.objects.create(user=self.user, key="k-1", method="POST", path="/api/app/v1/shop/orders", status_code=0)
        with self.assertRaises(ApiError) as caught:
            idempotent(self.request(), self.work())
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "request_in_progress"))

    def test_key_reused_for_another_endpoint(self):
        idempotent(self.request(), self.work())
        with self.assertRaises(ApiError) as caught:
            idempotent(self.request(path="/api/app/v1/forms/x/submit"), self.work())
        self.assertEqual(caught.exception.code, "idempotency_key_reused")

    def test_expired_records_are_forgotten(self):
        idempotent(self.request(), self.work())
        IdempotencyRecord.objects.update(created=timezone.now() - datetime.timedelta(days=2))
        idempotent(self.request(), self.work())
        self.assertEqual(self.runs, 2)


class PaginationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="parent@example.com", password=PASSWORD)
        now = timezone.now()
        for index in range(7):
            record = IdempotencyRecord.objects.create(user=self.user, key=f"k{index}", method="POST", path="/", status_code=200)
            # Two rows share a timestamp, to exercise the id tie-breaker.
            IdempotencyRecord.objects.filter(pk=record.pk).update(created=now - datetime.timedelta(minutes=index // 2 * 2 if index in (2, 3) else index))

    def test_walks_every_row_exactly_once(self):
        seen, cursor = [], None
        while True:
            page = paginate(IdempotencyRecord.objects.all(), sort_field="-created", cursor=cursor, limit=3)
            seen += [row.key for row in page.items]
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        self.assertEqual(sorted(seen), [f"k{index}" for index in range(7)])
        self.assertEqual(len(seen), 7)

    def test_bad_cursor(self):
        with self.assertRaises(ApiError) as caught:
            paginate(IdempotencyRecord.objects.all(), sort_field="-created", cursor="!!!")
        self.assertEqual(caught.exception.code, "invalid_cursor")

    def test_limit_is_clamped(self):
        page = paginate(IdempotencyRecord.objects.all(), sort_field="created", limit=1000)
        self.assertEqual(len(page.items), 7)
        self.assertIsNone(page.next_cursor)


@override_settings(APP_PUSH_SNS_APNS_ARN="arn:aws:sns:eu-west-1:1:app/APNS/rosterchief", APP_PUSH_SNS_APNS_SANDBOX_ARN="arn:aws:sns:eu-west-1:1:app/APNS_SANDBOX/rosterchief")
class SnsGatewayTests(TestCase):
    def setUp(self):
        import boto3

        self.user = User.objects.create_user(email="parent@example.com", password=PASSWORD)
        self.client_ = boto3.client("sns", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x")
        self.stub = Stubber(self.client_)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)
        self.gateway = SnsGateway(client=self.client_)
        self.device = PushDevice(user=self.user, installation_id="install-1", platform="ios", push_token="a1b2")

    def test_register_creates_an_endpoint(self):
        self.stub.add_response("create_platform_endpoint", {"EndpointArn": "arn:endpoint/1"}, {"PlatformApplicationArn": "arn:aws:sns:eu-west-1:1:app/APNS/rosterchief", "Token": "a1b2", "CustomUserData": str(self.user.pk)})
        self.assertEqual(self.gateway.register(self.device), "arn:endpoint/1")

    def test_sandbox_builds_use_the_sandbox_application(self):
        self.device.environment = PushDevice.Environment.SANDBOX
        self.stub.add_response("create_platform_endpoint", {"EndpointArn": "arn:endpoint/2"}, {"PlatformApplicationArn": "arn:aws:sns:eu-west-1:1:app/APNS_SANDBOX/rosterchief", "Token": "a1b2", "CustomUserData": str(self.user.pk)})
        self.assertEqual(self.gateway.register(self.device), "arn:endpoint/2")

    def test_existing_endpoint_for_the_token_is_reused(self):
        existing = "arn:aws:sns:eu-west-1:1:endpoint/APNS/rosterchief/abc"
        self.stub.add_client_error("create_platform_endpoint", "InvalidParameter", f"Invalid parameter: Token Reason: Endpoint {existing} already exists with the same Token, but different attributes.")
        self.stub.add_response("set_endpoint_attributes", {}, {"EndpointArn": existing, "Attributes": {"Token": "a1b2", "Enabled": "true", "CustomUserData": str(self.user.pk)}})
        self.assertEqual(self.gateway.register(self.device), existing)

    def test_publish_to_a_disabled_endpoint_reports_dead(self):
        self.device.endpoint_arn = "arn:endpoint/1"
        self.stub.add_client_error("publish", "EndpointDisabled", "Endpoint is disabled")
        self.assertFalse(self.gateway.publish(self.device, title="t", body="b", data={}))

    def test_apns_message_shape(self):
        message = json.loads(build_message(self.device, title="New event", body="Training Tuesday", data={"club_slug": "ajax-united"}))
        apns = json.loads(message["APNS"])
        self.assertEqual(apns["aps"]["alert"], {"title": "New event", "body": "Training Tuesday"})
        self.assertEqual(apns["club_slug"], "ajax-united")


@override_settings(**HOSTS)
class DocsTests(TestCase):
    def test_guide_pages(self):
        for path in ("/api/docs/", "/api/docs/ios/", "/api/docs/errors/", "/api/docs/status/"):
            response = self.client.get(path, HTTP_HOST=BASE)
            self.assertEqual(response.status_code, 200, path)
        self.assertContains(self.client.get("/api/docs/ios/", HTTP_HOST=BASE), "Signing in")

    def test_unknown_page_404_and_club_host_redirects(self):
        make_club()
        self.assertEqual(self.client.get("/api/docs/secrets/", HTTP_HOST=BASE).status_code, 404)
        redirect = self.client.get("/api/docs/ios/", HTTP_HOST=CLUB_HOST)
        self.assertRedirects(redirect, f"http://{BASE}/api/docs/ios/", fetch_redirect_response=False)

    def test_both_specs_are_served(self):
        app_spec = self.client.get("/api/app/v1/openapi.json", HTTP_HOST=BASE)
        auth_spec = self.client.get("/api/auth/openapi.json", HTTP_HOST=BASE)
        self.assertEqual(app_spec.status_code, 200)
        self.assertEqual(auth_spec.status_code, 200)
        self.assertIn("/api/auth/app/v1/auth/login", auth_spec.json()["paths"])
        self.assertEqual(self.client.get("/api/app/v1/docs", HTTP_HOST=BASE).status_code, 200)

    def test_openapi_snapshot_is_current(self):
        """docs/app-api/openapi-app-v1.json is what app developers generate their client
        from. Regenerate it with `manage.py export_app_openapi` when the API changes."""
        current = render_schema()
        self.assertTrue(SNAPSHOT.exists(), "Run `manage.py export_app_openapi` to create the snapshot.")
        self.assertEqual(SNAPSHOT.read_text(), current, "The app API changed: run `manage.py export_app_openapi` and commit the snapshot.")

    def test_export_command_writes_the_snapshot(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "spec.json"
            call_command("export_app_openapi", "--output", str(target), stdout=io.StringIO())
            self.assertEqual(json.loads(target.read_text())["info"]["title"], "RosterChief app API")


class SnsCredentialsTests(TestCase):
    @override_settings(APP_PUSH_SNS_REGION="eu-west-1", APP_PUSH_SNS_ACCESS_KEY_ID="AKIAPUSH", APP_PUSH_SNS_SECRET_ACCESS_KEY="secret")
    def test_dedicated_key_is_used(self):
        from unittest.mock import patch

        with patch("boto3.client") as client:
            SnsGateway()
        client.assert_called_once_with("sns", region_name="eu-west-1", aws_access_key_id="AKIAPUSH", aws_secret_access_key="secret")

    @override_settings(APP_PUSH_SNS_REGION="", APP_PUSH_SNS_ACCESS_KEY_ID="")
    def test_without_a_key_boto_uses_its_default_chain(self):
        from unittest.mock import patch

        with patch("boto3.client") as client:
            SnsGateway()
        client.assert_called_once_with("sns", region_name=None)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="localhost", ALLOWED_HOSTS=["localhost", ".localhost"])
class DevServerHostTests(TestCase):
    """On a dev server the port lives in the Host header (localhost:8000)."""

    def test_docs_redirect_and_club_urls_keep_the_port(self):
        from .services import club_api_base_url

        club = make_club()
        redirect = self.client.get("/api/docs/", HTTP_HOST="ajax-united.localhost:8000")
        self.assertEqual(redirect["Location"], "http://localhost:8000/api/docs/")

        request = RequestFactory().get("/", HTTP_HOST="localhost:8000")
        self.assertEqual(club_api_base_url(request, club), "http://ajax-united.localhost:8000/api/app/v1")


@override_settings(**HOSTS)
class ClubColorsTests(AppApiTestCase):
    def test_defaults_when_the_club_set_none(self):
        item = self.api("get", "/clubs", token=self.login()).json()["items"][0]
        self.assertEqual(
            {key: item[key] for key in ("primary_color", "primary_content_color", "secondary_color", "secondary_content_color")},
            {"primary_color": "#101e36", "primary_content_color": "#ffffff", "secondary_color": "#e4002b", "secondary_content_color": "#ffffff"},
        )

    def test_club_colours_with_readable_text(self):
        Club.objects.filter(pk=self.club.pk).update(primary_color="#1e40af", secondary_color="#fde047")
        token = self.login()

        item = self.api("get", "/clubs", token=token).json()["items"][0]
        club = self.api("get", "/context", token=token, host=CLUB_HOST).json()["club"]

        self.assertEqual((item["primary_color"], item["primary_content_color"]), ("#1e40af", "#ffffff"))
        self.assertEqual((item["secondary_color"], item["secondary_content_color"]), ("#fde047", "#000000"))
        self.assertEqual({key: club[key] for key in item if key.endswith("color")}, {key: item[key] for key in item if key.endswith("color")})


@override_settings(**HOSTS)
class EventBackgroundTests(AppApiTestCase):
    def test_absent_then_present_on_clubs_and_context(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from .test_member import png_bytes

        token = self.login()
        self.assertIsNone(self.api("get", "/clubs", token=token).json()["items"][0]["event_background_url"])

        self.club.event_background = SimpleUploadedFile("rink.png", png_bytes(), content_type="image/png")
        self.club.save()

        from_clubs = self.api("get", "/clubs", token=token).json()["items"][0]["event_background_url"]
        from_context = self.api("get", "/context", token=token, host=CLUB_HOST).json()["club"]["event_background_url"]
        self.assertTrue(from_clubs.startswith("http://") and from_clubs.endswith(".png"), from_clubs)
        self.assertEqual(from_clubs.rsplit("/", 1)[1], from_context.rsplit("/", 1)[1])
