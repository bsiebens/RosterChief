from django.core import mail, signing
from django.test import TestCase
from django.urls import reverse

from .forms import fresh_timestamp_token
from .models import ContactRequest

VALID_DATA = {
    "name": "Bernard Siebens",
    "email": "bernard@example.com",
    "club": "Sharks Mechelen",
    "members": "312",
    "role": ContactRequest.Role.BOARD_SECRETARY,
    "message": "We chase attendance in six WhatsApp groups.",
    "plan": "",
    "website": "",
}


class MarketingHomeViewTests(TestCase):
    def test_get_renders_the_marketing_homepage(self):
        response = self.client.get(reverse("root"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "marketing/home.html")
        self.assertContains(response, "Run the club, not the spreadsheet")

    def test_plan_query_param_is_carried_into_the_hidden_field(self):
        response = self.client.get(reverse("root") + "?plan=club")

        self.assertContains(response, 'value="club"')

    def test_unknown_plan_query_param_is_dropped(self):
        response = self.client.get(reverse("root") + "?plan=not-a-real-plan")

        self.assertNotContains(response, "not-a-real-plan")

    def test_valid_submission_creates_a_contact_request_and_sends_mail(self):
        data = {**VALID_DATA, "ts": fresh_timestamp_token()}

        response = self.client.post(reverse("root"), data)

        self.assertRedirects(response, reverse("root") + "#contact", fetch_redirect_response=False)
        contact = ContactRequest.objects.get()
        self.assertEqual(contact.name, "Bernard Siebens")
        self.assertEqual(contact.email, "bernard@example.com")
        self.assertEqual(contact.club, "Sharks Mechelen")
        self.assertEqual(contact.members, 312)
        self.assertEqual(contact.role, ContactRequest.Role.BOARD_SECRETARY)

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn("Sharks Mechelen", sent.subject)
        self.assertEqual(sent.reply_to, ["bernard@example.com"])

    def test_plan_interest_is_recorded_and_named_in_the_notification(self):
        data = {**VALID_DATA, "ts": fresh_timestamp_token(), "plan": "club"}

        self.client.post(reverse("root"), data)

        contact = ContactRequest.objects.get()
        self.assertEqual(contact.plan_interest, "club")
        self.assertIn("club", mail.outbox[0].body)

    def test_success_state_replaces_the_form_after_redirect(self):
        data = {**VALID_DATA, "ts": fresh_timestamp_token()}
        self.client.post(reverse("root"), data)

        response = self.client.get(reverse("root"))

        self.assertContains(response, "Thanks")
        # Not "<form" -- the header's language switcher is also a <form>, and it's
        # always present. Check for the contact form specifically instead.
        self.assertNotContains(response, 'name="ts"')

    def test_a_second_get_no_longer_shows_the_success_state(self):
        data = {**VALID_DATA, "ts": fresh_timestamp_token()}
        self.client.post(reverse("root"), data)
        self.client.get(reverse("root"))

        response = self.client.get(reverse("root"))

        self.assertContains(response, 'name="ts"')

    def test_honeypot_rejects_the_submission(self):
        data = {**VALID_DATA, "ts": fresh_timestamp_token(), "website": "https://spam.example"}

        response = self.client.post(reverse("root"), data)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(ContactRequest.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_tampered_timestamp_rejects_the_submission(self):
        data = {**VALID_DATA, "ts": "not-a-real-signed-value"}

        response = self.client.post(reverse("root"), data)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(ContactRequest.objects.exists())

    def test_a_token_signed_under_a_different_salt_rejects_the_submission(self):
        # Proves the salt is actually checked, not just that *some* signature is
        # present -- a token lifted from an unrelated signed form must not pass.
        data = {**VALID_DATA, "ts": signing.dumps("marketing-contact", salt="some.other.form")}

        response = self.client.post(reverse("root"), data)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(ContactRequest.objects.exists())

    def test_invalid_submission_redisplays_the_form_with_errors(self):
        data = {**VALID_DATA, "ts": fresh_timestamp_token(), "email": "not-an-email"}

        response = self.client.post(reverse("root"), data)

        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "marketing/home.html")
        self.assertFalse(ContactRequest.objects.exists())
