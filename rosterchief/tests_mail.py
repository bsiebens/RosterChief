from unittest import mock

import requests
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

from rosterchief.mail import RESEND_API_URL, ResendEmailBackend

MAILERS = {"default": {"BACKEND": "rosterchief.mail.ResendEmailBackend", "OPTIONS": {"api_key": "re_test"}}}


def session_returning(status=200):
    session = mock.MagicMock()
    session.__enter__.return_value = session
    response = mock.Mock(status_code=status)
    response.raise_for_status.side_effect = None if status < 400 else requests.HTTPError("boom")
    session.post.return_value = response
    return session


class ResendBackendTests(SimpleTestCase):
    def test_a_templated_mail_goes_to_resend_with_text_html_recipients_and_attachment(self):
        message = EmailMultiAlternatives("Welcome", "Plain body", "Club <club@example.com>", ["a@example.com"], cc=["c@example.com"], bcc=["b@example.com"], reply_to=["r@example.com"])
        message.attach_alternative("<p>HTML body</p>", "text/html")
        message.attach("invoice.pdf", b"%PDF-1", "application/pdf")
        session = session_returning()
        with mock.patch("rosterchief.mail.requests.Session", return_value=session):
            sent = ResendEmailBackend(api_key="re_test").send_messages([message])
        self.assertEqual(sent, 1)
        (url,), kwargs = session.post.call_args
        self.assertEqual(url, RESEND_API_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer re_test")
        body = kwargs["json"]
        self.assertEqual((body["from"], body["to"], body["cc"], body["bcc"], body["reply_to"]), ("Club <club@example.com>", ["a@example.com"], ["c@example.com"], ["b@example.com"], ["r@example.com"]))
        self.assertEqual((body["subject"], body["text"], body["html"]), ("Welcome", "Plain body", "<p>HTML body</p>"))
        self.assertEqual(body["attachments"], [{"filename": "invoice.pdf", "content": "JVBERi0x"}])

    def test_it_is_picked_up_from_the_mailers_setting_like_in_production(self):
        session = session_returning()
        with override_settings(MAILERS=MAILERS), mock.patch("rosterchief.mail.requests.Session", return_value=session):
            self.assertEqual(EmailMessage("Hi", "Body", "club@example.com", ["a@example.com"]).send(), 1)
        self.assertEqual(session.post.call_args.kwargs["headers"]["Authorization"], "Bearer re_test")

    def test_a_failure_raises_unless_the_caller_asked_for_silence(self):
        message = EmailMessage("Hi", "Body", "club@example.com", ["a@example.com"])
        with mock.patch("rosterchief.mail.requests.Session", return_value=session_returning(500)):
            with self.assertRaises(requests.HTTPError):
                ResendEmailBackend(api_key="re_test").send_messages([message])
            self.assertEqual(ResendEmailBackend(fail_silently=True, api_key="re_test").send_messages([message]), 0)

    def test_without_an_api_key_nothing_is_sent_and_it_says_so(self):
        message = EmailMessage("Hi", "Body", "club@example.com", ["a@example.com"])
        with mock.patch("rosterchief.mail.requests.Session") as session:
            with self.assertRaises(ValueError):
                ResendEmailBackend().send_messages([message])
            self.assertEqual(ResendEmailBackend(fail_silently=True).send_messages([message]), 0)
        session.assert_not_called()
