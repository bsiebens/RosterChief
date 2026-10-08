import importlib
import tempfile
from pathlib import Path
from unittest import mock

import requests
from django.core import mail as django_mail
from django.core.cache import cache
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.db import connection
from django.db.utils import OperationalError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import Resolver404, clear_url_caches, resolve, reverse

from features.models import EmailSuppression

from . import urls
from .mail import ResendEmailBackend, send_message
from .media import serve_media


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


class SendMessageTests(TestCase):
    """rosterchief.mail.send_message -- the choke point every automated send in
    the app routes through (see the function's own docstring for the full list
    of call sites), gated on features.models.EmailSuppression."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def message(self):
        return EmailMessage("Subject", "Body.", "from@example.com", ["to@example.com"])

    def test_sends_normally_when_not_suppressed(self):
        sent = send_message(self.message())

        self.assertTrue(sent)
        self.assertEqual(len(django_mail.outbox), 1)

    def test_is_silenced_while_suppressed(self):
        EmailSuppression.start()

        sent = send_message(self.message())

        self.assertFalse(sent)
        self.assertEqual(len(django_mail.outbox), 0)

    def test_exempt_still_sends_while_suppressed(self):
        EmailSuppression.start()

        sent = send_message(self.message(), exempt=True)

        self.assertTrue(sent)
        self.assertEqual(len(django_mail.outbox), 1)

    def test_resuming_lets_mail_through_again(self):
        EmailSuppression.start()
        EmailSuppression.stop()

        sent = send_message(self.message())

        self.assertTrue(sent)
        self.assertEqual(len(django_mail.outbox), 1)


@override_settings(RESEND_API_KEY="test-key")
class ResendEmailBackendTests(SimpleTestCase):
    def ok_response(self):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        return response

    def test_sends_a_plain_text_message(self):
        message = EmailMessage("Subject", "Body text.", "from@example.com", ["to@example.com"])

        with mock.patch("rosterchief.mail.requests.Session.post", return_value=self.ok_response()) as post:
            sent = ResendEmailBackend().send_messages([message])

        self.assertEqual(sent, 1)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["from"], "from@example.com")
        self.assertEqual(payload["to"], ["to@example.com"])
        self.assertEqual(payload["subject"], "Subject")
        self.assertEqual(payload["text"], "Body text.")
        self.assertNotIn("html", payload)

    def test_uses_a_bearer_token_from_settings(self):
        message = EmailMessage("Subject", "Body.", "from@example.com", ["to@example.com"])

        with mock.patch("rosterchief.mail.requests.Session.post", return_value=self.ok_response()) as post:
            ResendEmailBackend().send_messages([message])

        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")

    def test_includes_the_html_alternative(self):
        message = EmailMultiAlternatives("Subject", "Plain body.", "from@example.com", ["to@example.com"])
        message.attach_alternative("<p>HTML body.</p>", "text/html")

        with mock.patch("rosterchief.mail.requests.Session.post", return_value=self.ok_response()) as post:
            ResendEmailBackend().send_messages([message])

        self.assertEqual(post.call_args.kwargs["json"]["html"], "<p>HTML body.</p>")

    def test_cc_bcc_and_reply_to_are_included(self):
        message = EmailMessage("Subject", "Body.", "from@example.com", ["to@example.com"], cc=["cc@example.com"], bcc=["bcc@example.com"], reply_to=["reply@example.com"])

        with mock.patch("rosterchief.mail.requests.Session.post", return_value=self.ok_response()) as post:
            ResendEmailBackend().send_messages([message])

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["cc"], ["cc@example.com"])
        self.assertEqual(payload["bcc"], ["bcc@example.com"])
        self.assertEqual(payload["reply_to"], ["reply@example.com"])

    def test_attachments_are_base64_encoded(self):
        message = EmailMessage("Subject", "Body.", "from@example.com", ["to@example.com"])
        message.attach("notes.txt", "hello world", "text/plain")

        with mock.patch("rosterchief.mail.requests.Session.post", return_value=self.ok_response()) as post:
            ResendEmailBackend().send_messages([message])

        [attachment] = post.call_args.kwargs["json"]["attachments"]
        self.assertEqual(attachment["filename"], "notes.txt")
        self.assertEqual(attachment["content"], "aGVsbG8gd29ybGQ=")

    def test_sends_each_message_in_a_batch(self):
        messages = [EmailMessage("A", "Body.", "from@example.com", ["a@example.com"]), EmailMessage("B", "Body.", "from@example.com", ["b@example.com"])]

        with mock.patch("rosterchief.mail.requests.Session.post", return_value=self.ok_response()) as post:
            sent = ResendEmailBackend().send_messages(messages)

        self.assertEqual(sent, 2)
        self.assertEqual(post.call_count, 2)

    @override_settings(RESEND_API_KEY="")
    def test_a_missing_api_key_raises_by_default(self):
        with self.assertRaises(ValueError):
            ResendEmailBackend().send_messages([EmailMessage("Subject", "Body.", "from@example.com", ["to@example.com"])])

    @override_settings(RESEND_API_KEY="")
    def test_a_missing_api_key_is_silent_when_fail_silently(self):
        sent = ResendEmailBackend(fail_silently=True).send_messages([EmailMessage("Subject", "Body.", "from@example.com", ["to@example.com"])])

        self.assertEqual(sent, 0)

    def test_a_failed_request_raises_by_default(self):
        message = EmailMessage("Subject", "Body.", "from@example.com", ["to@example.com"])

        with mock.patch("rosterchief.mail.requests.Session.post", side_effect=requests.ConnectionError("down")):
            with self.assertRaises(requests.ConnectionError):
                ResendEmailBackend().send_messages([message])

    def test_a_failed_request_is_silent_when_fail_silently(self):
        message = EmailMessage("Subject", "Body.", "from@example.com", ["to@example.com"])

        with mock.patch("rosterchief.mail.requests.Session.post", side_effect=requests.ConnectionError("down")):
            sent = ResendEmailBackend(fail_silently=True).send_messages([message])

        self.assertEqual(sent, 0)

    def test_no_messages_is_a_no_op(self):
        with mock.patch("rosterchief.mail.requests.Session.post") as post:
            sent = ResendEmailBackend().send_messages([])

        self.assertEqual(sent, 0)
        post.assert_not_called()


class PerformanceLoggingMiddlewareTests(SimpleTestCase):
    """rosterchief/middleware.py + rosterchief/perf_logging.py -- opt-in request
    tracing built to diagnose the control panel dashboard's slow loads without
    installing django-debug-toolbar in production."""

    databases = {"default"}

    @override_settings(PERFORMANCE_LOGGING_ENABLED=False)
    def test_disabled_by_default_logs_nothing(self):
        with self.assertNoLogs("rosterchief.performance", level="WARNING"):
            self.client.get(reverse("healthz"))

    @override_settings(PERFORMANCE_LOGGING_ENABLED=True, PERFORMANCE_LOGGING_THRESHOLD_MS=0)
    def test_enabled_and_over_threshold_logs_a_slow_request(self):
        with self.assertLogs("rosterchief.performance", level="WARNING") as captured:
            self.client.get(reverse("healthz"))

        self.assertIn("slow_request", captured.output[0])
        self.assertIn("path=/healthz", captured.output[0])
        self.assertIn("db_queries=", captured.output[0])

    @override_settings(PERFORMANCE_LOGGING_ENABLED=True, PERFORMANCE_LOGGING_THRESHOLD_MS=10_000)
    def test_under_threshold_logs_nothing(self):
        with self.assertNoLogs("rosterchief.performance", level="WARNING"):
            self.client.get(reverse("healthz"))


class PerfLoggingQueryTrackingTests(TestCase):
    """query_execute_wrapper + start_tracking/stop_tracking, exercised directly
    rather than through a full request -- see PerformanceLoggingMiddlewareTests
    for the request-level behaviour."""

    def test_a_no_op_outside_a_tracked_block(self):
        from django.contrib.auth import get_user_model

        from .perf_logging import query_execute_wrapper, stop_tracking

        with connection.execute_wrapper(query_execute_wrapper):
            get_user_model().objects.count()

        # Nothing was tracking (start_tracking() was never called), so this must
        # not have accumulated anything to leak into a later, real request.
        queries, template_ms = stop_tracking()
        self.assertEqual(queries, [])
        self.assertEqual(template_ms, 0.0)

    def test_records_each_query_inside_a_tracked_block(self):
        from django.contrib.auth import get_user_model

        from .perf_logging import query_execute_wrapper, start_tracking, stop_tracking

        start_tracking()
        with connection.execute_wrapper(query_execute_wrapper):
            get_user_model().objects.count()
            get_user_model().objects.count()
        queries, _template_ms = stop_tracking()

        self.assertEqual(len(queries), 2)
        for elapsed, sql in queries:
            self.assertGreaterEqual(elapsed, 0)
            self.assertIn("SELECT", sql.upper())


class PerfLoggingTemplateTimingTests(SimpleTestCase):
    """Template.render is patched once, at import time, only when
    PERFORMANCE_LOGGING_ENABLED -- these call the patched function directly rather
    than depending on import-time settings, since that patch either did or didn't
    already happen before the test suite started."""

    def test_a_top_level_render_adds_to_the_total(self):
        from django.template import Context
        from django.template import Template as DjangoTemplate

        from .perf_logging import _timed_template_render, start_tracking, stop_tracking

        template = DjangoTemplate("hello {{ name }}")

        start_tracking()
        _timed_template_render(template, Context({"name": "one"}))
        _queries, template_ms = stop_tracking()

        self.assertGreater(template_ms, 0)

    def test_a_nested_render_does_not_add_to_the_total(self):
        # Simulates being called from inside an outer Template.render (an
        # {% include %}/{% extends %}'d template's own render() call nests inside
        # its parent's) -- depth already > 0 on entry, same as it would be there.
        # The outer call's own elapsed time already covers this one; counting both
        # would double-count every nested template.
        from django.template import Context
        from django.template import Template as DjangoTemplate

        from .perf_logging import _state, _timed_template_render, start_tracking, stop_tracking

        template = DjangoTemplate("hello {{ name }}")

        start_tracking()
        _state.template_depth = 1
        _timed_template_render(template, Context({"name": "inner"}))
        _queries, template_ms = stop_tracking()

        self.assertEqual(template_ms, 0.0)

    def test_depth_returns_to_zero_after_a_top_level_render(self):
        from django.template import Context
        from django.template import Template as DjangoTemplate

        from .perf_logging import _state, _timed_template_render, start_tracking

        template = DjangoTemplate("hello {{ name }}")

        start_tracking()
        _timed_template_render(template, Context({"name": "one"}))

        # Checked before stop_tracking() (which would reset it to 0 regardless) --
        # a leaked depth counter here would corrupt every later render in the same
        # request, silently dropping or double-counting its time.
        self.assertEqual(_state.template_depth, 0)


class ServeMediaTests(SimpleTestCase):
    """rosterchief.media.serve_media -- the Django fallback for /media/* adds the
    same hardening headers as the Caddy media block."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (root / "logo.svg").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')

    def _get(self, path):
        return serve_media(RequestFactory().get(f"/media/{path}"), path, document_root=self.tmp.name)

    def test_every_file_gets_nosniff_and_a_sandboxing_csp(self):
        response = self._get("logo.png")

        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", response["Content-Security-Policy"])
        self.assertFalse(response.get("Content-Disposition", "").startswith("attachment"))

    def test_svg_is_served_as_an_attachment(self):
        response = self._get("logo.svg")

        self.assertEqual(response["Content-Disposition"], "attachment")
        self.assertIn("sandbox", response["Content-Security-Policy"])


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", ".rosterchief.app", "testserver"], CSRF_TRUSTED_ORIGINS=[])
class CsrfCookieTossingTests(TestCase):
    """The CSRF secret is kept in the session (CSRF_USE_SESSIONS), so a csrftoken
    cookie planted from a sibling subdomain ("cookie tossing") can't be paired
    with a known form token to forge a POST."""

    def setUp(self):
        from django.test import Client

        cache.clear()
        self.addCleanup(cache.clear)
        self.client = Client(enforce_csrf_checks=True)

    def test_the_csrf_secret_is_stored_in_the_session(self):
        from django.conf import settings

        self.assertTrue(settings.CSRF_USE_SESSIONS)

    def test_a_tossed_csrf_cookie_does_not_validate_a_post(self):
        token = "a" * 32
        self.client.cookies["csrftoken"] = token

        response = self.client.post(reverse("account_login"), {"login": "x@example.com", "password": "x", "csrfmiddlewaretoken": token})

        self.assertEqual(response.status_code, 403)

    def test_a_token_from_the_rendered_form_still_validates(self):
        page = self.client.get(reverse("account_login"))
        token = page.context["csrf_token"]

        response = self.client.post(reverse("account_login"), {"login": "x@example.com", "password": "x", "csrfmiddlewaretoken": str(token)})

        self.assertNotEqual(response.status_code, 403)
        self.assertNotIn("csrftoken", response.cookies)

    def test_a_post_from_a_sibling_subdomain_origin_is_rejected(self):
        page = self.client.get(reverse("account_login"), HTTP_HOST="ajax-united.rosterchief.app")
        token = str(page.context["csrf_token"])

        response = self.client.post(reverse("account_login"), {"login": "x@example.com", "password": "x", "csrfmiddlewaretoken": token}, HTTP_HOST="ajax-united.rosterchief.app", HTTP_ORIGIN="http://evil.rosterchief.app")

        self.assertEqual(response.status_code, 403)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", ".rosterchief.app", "testserver"])
class AdminBaseDomainOnlyTests(TestCase):
    """Django's admin manages every club at once, so it only answers on the base
    domain; on any club subdomain (known or not) it is a plain 404."""

    @classmethod
    def setUpTestData(cls):
        from rosterchief.test_support import make_club

        make_club()

    def test_admin_login_404s_on_a_club_host(self):
        response = self.client.get("/admin/login/", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_admin_index_404s_on_a_club_host(self):
        response = self.client.get("/admin/", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_admin_model_page_404s_on_a_club_host(self):
        response = self.client.get("/admin/authentication/user/", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_admin_404s_on_an_unknown_subdomain(self):
        response = self.client.get("/admin/", HTTP_HOST="no-such-club.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_admin_404s_for_a_signed_in_superuser_on_a_club_host(self):
        from django.contrib.auth import get_user_model

        from rosterchief.test_support import enrol_mfa

        user = get_user_model().objects.create_superuser(email="root@example.com", password="pw-secret-123")
        enrol_mfa(user)
        self.client.force_login(user)

        response = self.client.get("/admin/", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_admin_login_redirects_on_the_base_domain(self):
        response = self.client.get("/admin/login/", HTTP_HOST="rosterchief.app")

        self.assertEqual(response.status_code, 302)

    def test_admin_index_redirects_to_login_on_the_base_domain(self):
        response = self.client.get("/admin/", HTTP_HOST="rosterchief.app")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_admin_index_is_served_to_a_superuser_on_the_base_domain(self):
        from django.contrib.auth import get_user_model

        from rosterchief.test_support import enrol_mfa

        user = get_user_model().objects.create_superuser(email="root@example.com", password="pw-secret-123")
        enrol_mfa(user)
        self.client.force_login(user)

        response = self.client.get("/admin/", HTTP_HOST="rosterchief.app")

        self.assertEqual(response.status_code, 200)


class SecretKeySystemCheckTests(SimpleTestCase):
    """rosterchief.E001: with DEBUG off, `check`/`migrate` refuse a placeholder SECRET_KEY."""

    STRONG_KEY = "k3J9vQ2xLr8TzWm4Pq7Yb1Nc6Hd0Fs5Ga-_Ue2Xo9Ri4Vt7Lp3Sw8Mz1Qy6Bn0Cj"

    def run_check(self, **overrides):
        from .checks import secret_key_check

        settings = {"DEBUG": False, "TESTING": False, "SECRET_KEY": self.STRONG_KEY, **overrides}
        with override_settings(**settings):
            return secret_key_check(None)

    def test_a_strong_key_passes(self):
        self.assertEqual(self.run_check(), [])

    def test_weak_keys_are_errors(self):
        weak = {
            "empty": "",
            "short": "a-perfectly-random-but-short-key",
            "few unique characters": "ab" * 40,
            "build placeholder": "build-only-not-a-secret" + "x" * 40,
            "changeme": "CHANGEME-" + self.STRONG_KEY,
            "django-insecure prefix": "django-insecure-" + self.STRONG_KEY,
        }
        for label, key in weak.items():
            with self.subTest(label):
                errors = self.run_check(SECRET_KEY=key)
                self.assertEqual([error.id for error in errors], ["rosterchief.E001"])

    def test_skipped_when_debug_is_on(self):
        self.assertEqual(self.run_check(DEBUG=True, SECRET_KEY="dev"), [])

    def test_skipped_under_the_test_runner(self):
        self.assertEqual(self.run_check(TESTING=True, SECRET_KEY="ci-test-not-a-secret"), [])

    def test_registered_with_the_check_framework(self):
        from django.core import checks

        from .checks import secret_key_check

        self.assertIn(secret_key_check, checks.registry.registry.get_checks())

    def test_hsts_preload_warning_is_the_only_silenced_check(self):
        from django.conf import settings

        self.assertEqual(settings.SILENCED_SYSTEM_CHECKS, ["security.W021"])


class FastSuiteSample(SimpleTestCase):
    """Two trivial tests the runner tests below load by label."""

    def test_a(self):
        pass

    def test_b(self):
        pass


class FastSuiteRunnerTests(SimpleTestCase):
    """`manage.py test --fast` (rosterchief.test_runner): skips the tests in fast_suite_skip.txt."""

    SAMPLE = "rosterchief.tests.FastSuiteSample"

    def ids(self, runner):
        from django.test.runner import iter_test_cases

        return sorted(test.id() for test in iter_test_cases(runner.load_tests_for_label(self.SAMPLE, {})))

    def runner(self, **kwargs):
        from .test_runner import RosterChiefTestRunner

        return RosterChiefTestRunner(**kwargs)

    def test_without_fast_nothing_is_skipped(self):
        self.assertEqual(self.ids(self.runner()), [f"{self.SAMPLE}.test_a", f"{self.SAMPLE}.test_b"])

    def test_fast_skips_the_listed_tests_and_keeps_every_other_one(self):
        runner = self.runner()
        runner.skip = frozenset({f"{self.SAMPLE}.test_a"})

        self.assertEqual(self.ids(runner), [f"{self.SAMPLE}.test_b"])

    def test_fast_combines_with_sharding(self):
        runner = self.runner(shard="1/1")
        runner.skip = frozenset({f"{self.SAMPLE}.test_b"})

        self.assertEqual(self.ids(runner), [f"{self.SAMPLE}.test_a"])

    def test_the_skip_file_ignores_comments_and_blank_lines(self):
        from .test_runner import read_fast_suite_skip

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skip.txt"
            path.write_text("# generated\n\n  app.tests.A.test_one  \n# another comment\napp.tests.B.test_two\n")

            self.assertEqual(read_fast_suite_skip(path), {"app.tests.A.test_one", "app.tests.B.test_two"})

    def test_a_missing_skip_file_means_nothing_is_skipped(self):
        from .test_runner import read_fast_suite_skip

        self.assertEqual(read_fast_suite_skip(Path("/nonexistent/fast_suite_skip.txt")), frozenset())

    def test_every_entry_in_the_real_skip_file_is_a_dotted_test_id(self):
        from .test_runner import read_fast_suite_skip

        skip = read_fast_suite_skip()

        self.assertTrue(skip, "the fast-suite skip list is empty; regenerate it with deploy/refresh_fast_suite.py")
        self.assertTrue(all(entry.count(".") >= 3 and " " not in entry for entry in skip))
