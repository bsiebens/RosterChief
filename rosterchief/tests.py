import importlib
from unittest import mock

import requests
from django.core import mail as django_mail
from django.core.cache import cache
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.db.utils import OperationalError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import Resolver404, clear_url_caches, resolve, reverse

from features.models import EmailSuppression

from . import urls
from .mail import ResendEmailBackend, send_message


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
