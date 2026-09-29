"""Phase 3 of the app API: payments, forms, photo upload, bug reports, calendar feed.
Same fixtures as test_member (a parent Pat with a child Kim, signed in)."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from bugs.models import BugNote, BugReport
from club.models import ClubMembership
from formbuilder.models import Field as FormField
from formbuilder.models import Form, FormSend, Submission
from mobile.models import CalendarFeedToken
from registration.models import RegistrationBatch, RegistrationDetails

from .models import IdempotencyRecord
from .test_member import ClubApiTestCase, png_bytes
from .tests import CLUB_HOST


class PaymentsTests(ClubApiTestCase):
    def confirmed_batch(self, **kwargs):
        number = RegistrationBatch.objects.count() + 1
        defaults = {
            "club": self.club,
            "season": self.season,
            "contact_first_name": "Pat",
            "contact_last_name": "Parent",
            "contact_email": "parent@example.com",
            "invoice_number": f"REG-{number}",
            "invoice_sent_at": timezone.now(),
            "invoice_due_date": timezone.localdate() + datetime.timedelta(days=14),
        }
        defaults.update(kwargs)
        return RegistrationBatch.objects.create(**defaults)

    def test_registration_groups_the_family(self):
        membership = ClubMembership.objects.get(member=self.child)
        ClubMembership.objects.filter(pk=membership.pk).update(fee_amount=Decimal("150.00"))
        batch = self.confirmed_batch()
        RegistrationDetails.objects.create(membership=membership, batch=batch, price=Decimal("150.00"))
        self.club.payment_instructions = "BE00 0000 0000 0000"
        self.club.save()

        body = self.get("/payments").json()

        self.assertEqual(body["payment_instructions"], "BE00 0000 0000 0000")
        item = body["items"][0]
        self.assertEqual((item["kind"], item["registration_id"]), ("registration", str(batch.pk)))
        self.assertEqual(item["total_balance"], {"amount": "150.00", "currency": "EUR"})
        self.assertEqual(item["members"][0]["member_id"], str(self.child.pk))
        self.assertEqual(item["members"][0]["lines"][0]["price"], {"amount": "150.00", "currency": "EUR"})

    def test_unconfirmed_registration_is_held_back(self):
        membership = ClubMembership.objects.get(member=self.child)
        ClubMembership.objects.filter(pk=membership.pk).update(fee_amount=Decimal("150.00"))
        RegistrationDetails.objects.create(membership=membership, batch=self.confirmed_batch(invoice_sent_at=None), price=Decimal("150.00"))
        self.assertEqual(self.get("/payments").json()["items"], [])

    def test_invoice_pdf(self):
        membership = ClubMembership.objects.get(member=self.child)
        batch = self.confirmed_batch()
        RegistrationDetails.objects.create(membership=membership, batch=batch, price=Decimal("150.00"))
        with patch("appapi.routers.payments.batch_invoice_pdf", return_value=b"%PDF-fake"):
            response = self.get(f"/payments/registrations/{batch.pk}/invoice.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response.content, b"%PDF-fake")

    def test_someone_elses_or_unconfirmed_invoice_is_404(self):
        stranger_membership = ClubMembership.objects.get(member=self.stranger())
        theirs = self.confirmed_batch()
        RegistrationDetails.objects.create(membership=stranger_membership, batch=theirs, price=Decimal("1"))
        unconfirmed = self.confirmed_batch(invoice_sent_at=None)
        RegistrationDetails.objects.create(membership=ClubMembership.objects.get(member=self.child), batch=unconfirmed, price=Decimal("1"))

        self.assertError(self.get(f"/payments/registrations/{theirs.pk}/invoice.pdf"), 404, "not_found")
        self.assertError(self.get(f"/payments/registrations/{unconfirmed.pk}/invoice.pdf"), 404, "not_found")


class FormsTests(ClubApiTestCase):
    def setUp(self):
        super().setUp()
        self.form = Form.objects.create(club=self.club, title="Medical info")
        FormField.objects.create(form=self.form, key="allergies", label="Allergies", field_type=FormField.FieldType.TEXT, required=True, order=1)
        FormField.objects.create(form=self.form, key="size", label="Shirt size", field_type=FormField.FieldType.CHOICE, options=["S", "M"], required=False, order=2)
        FormField.objects.create(form=self.form, key="document", label="Certificate", field_type=FormField.FieldType.FILE, required=False, order=3)
        self.send = FormSend.objects.create(club=self.club, form=self.form)
        self.send.invited_members.add(self.child)

    def submit(self, data, **headers):
        return self.api("post", f"/forms/{self.send.pk}/submit", token=self.token, host=CLUB_HOST, data=data, **headers)

    def test_list_and_schema(self):
        rows = self.get("/forms").json()["items"]
        self.assertEqual([(row["title"], row["member_id"], row["submitted_at"]) for row in rows], [("Medical info", str(self.child.pk), None)])

        body = self.get(f"/forms/{self.send.pk}").json()
        self.assertEqual([field["key"] for field in body["fields"]], ["allergies", "size", "document"])
        self.assertEqual(body["fields"][1]["options"], [{"value": "S", "label": "S"}, {"value": "M", "label": "M"}])
        self.assertEqual(body["addressed"], [{"member_id": str(self.child.pk), "submissions": 0}])
        self.assertTrue(body["is_open"])

    def test_json_submit_for_a_child_and_read_it_back(self):
        response = self.submit({"member_id": str(self.child.pk), "answers": {"allergies": "Peanuts", "size": "M"}}, HTTP_IDEMPOTENCY_KEY="k-1")

        self.assertEqual(response.status_code, 201, response.content)
        submission = Submission.objects.get()
        self.assertEqual((submission.member, submission.submitted_by), (self.child, self.member))
        answers = {answer["key"]: answer["value"] for answer in self.get(f"/forms/submissions/{submission.pk}").json()["answers"]}
        self.assertEqual(answers, {"allergies": "Peanuts", "size": "M", "document": None})

        replay = self.submit({"member_id": str(self.child.pk), "answers": {"allergies": "Peanuts", "size": "M"}}, HTTP_IDEMPOTENCY_KEY="k-1")
        self.assertEqual(replay.json()["id"], response.json()["id"])
        self.assertEqual(Submission.objects.count(), 1)

    def test_multipart_submit_with_a_file(self):
        upload = SimpleUploadedFile("cert.pdf", b"%PDF-1.4 fake", content_type="application/pdf")
        response = self.client.post(f"/api/app/v1/forms/{self.send.pk}/submit", {"member_id": str(self.child.pk), "allergies": "None", "document": upload}, HTTP_HOST=CLUB_HOST, HTTP_AUTHORIZATION=f"Bearer {self.token}")

        self.assertEqual(response.status_code, 201, response.content)
        document = next(answer for answer in response.json()["answers"] if answer["key"] == "document")
        self.assertEqual(document["value"]["name"], "cert.pdf")

    def test_validation_and_closed(self):
        response = self.submit({"member_id": str(self.child.pk), "answers": {"size": "XXL"}})
        self.assertError(response, 422, "validation_error")
        self.assertEqual(set(response.json()["error"]["fields"]), {"allergies", "size"})
        self.assertFalse(IdempotencyRecord.objects.exists())

        FormSend.objects.filter(pk=self.send.pk).update(closes_at=timezone.now() - datetime.timedelta(days=1))
        self.assertError(self.submit({"member_id": str(self.child.pk), "answers": {"allergies": "x"}}), 409, "form_unavailable")

    def test_not_addressed(self):
        self.assertError(self.submit({"member_id": str(self.member.pk), "answers": {"allergies": "x"}}), 404, "not_found")
        other = FormSend.objects.create(club=self.club, form=self.form)
        other.invited_members.add(self.stranger())
        self.assertError(self.get(f"/forms/{other.pk}"), 404, "not_found")


class PhotoUploadTests(ClubApiTestCase):
    def upload(self, **data):
        return self.client.post(f"/api/app/v1/members/{self.child.pk}/photo", data, HTTP_HOST=CLUB_HOST, HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def test_upload_replace_and_delete(self):
        response = self.upload(photo=SimpleUploadedFile("kim.png", png_bytes(), content_type="image/png"), photo_public_consent="true")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNotNone(response.json()["photo_url"])
        self.assertTrue(response.json()["photo_public_consent"])

        removed = self.send("delete", f"/members/{self.child.pk}/photo")
        self.assertIsNone(removed.json()["photo_url"])
        self.assertTrue(removed.json()["photo_public_consent"])

    def test_not_an_image(self):
        response = self.upload(photo=SimpleUploadedFile("kim.png", b"not an image", content_type="image/png"))
        self.assertError(response, 422, "validation_error")
        self.assertIn("photo", response.json()["error"]["fields"])

    def test_missing_file(self):
        self.assertError(self.upload(), 422, "validation_error")


class SupportTests(ClubApiTestCase):
    def test_file_and_list_bug_reports(self):
        created = self.send("post", "/bug-reports", {"title": "Crash", "description": "On the calendar"})
        self.assertEqual(created.status_code, 201)
        bug = BugReport.objects.get()
        self.assertEqual(bug.reported_by, self.member)
        BugNote.objects.create(bug=bug, body="Looking into it")

        items = self.get("/bug-reports").json()["items"]
        self.assertEqual([(item["title"], item["status"]) for item in items], [("Crash", "submitted")])
        self.assertEqual(items[0]["notes"][0]["body"], "Looking into it")
        self.assertNotIn("priority", items[0])

    def test_calendar_feed_link(self):
        self.assertEqual(self.get("/calendar-feed").json(), {"has_feed": False})

        first = self.send("post", "/calendar-feed").json()
        second = self.send("post", "/calendar-feed").json()

        self.assertTrue(first["webcal_url"].startswith("webcal://ajax-united.rosterchief.app/app/calendar/"))
        self.assertNotEqual(first["feed_url"], second["feed_url"])
        self.assertEqual(CalendarFeedToken.objects.count(), 1)
        feed = self.client.get(second["feed_url"].replace("http://ajax-united.rosterchief.app", ""), HTTP_HOST=CLUB_HOST)
        self.assertEqual(feed.status_code, 200)
        old = self.client.get(first["feed_url"].replace("http://ajax-united.rosterchief.app", ""), HTTP_HOST=CLUB_HOST)
        self.assertEqual(old.status_code, 404)
