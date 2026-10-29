import datetime
import uuid
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import override_settings

from api.protected.tests import CLUB_HOST, AppApiTestCase
from club.models import Club, ClubMembership, ClubRole, Season
from registration.models import RegistrationBatch
from registration.services import EntryInput, submit_registration
from registration.services.invoicing import RegistrationInvoicePDFError
from shop.models import Product, ProductVariant
from teams.models import NumberPool, Position, StaffAssignment, Team, TeamMembership

BASE = "/manage/registrations"
MAIL = override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")


class RegistrationsTestCase(AppApiTestCase):
    def setUp(self):
        super().setUp()
        self._token = None
        product = Product.objects.create(club=self.club, name="Player Registration", product_type=Product.ProductType.MEMBERSHIP, season=self.season, price=Decimal("80.00"))
        self.variant = ProductVariant.objects.create(product=product, name="Standard", price=Decimal("80.00"))
        self.batch = self.make_batch("reg-parent@example.com", [("Kid", "One")])

    def make_batch(self, email, names, club=None, season=None, variant=None):
        entries = [EntryInput(first_name=first, last_name=last, date_of_birth=datetime.date(2016, 5, 1), product_variant=variant or self.variant) for first, last in names]
        return submit_registration(club or self.club, contact_first_name="Reg", contact_last_name="Parent", contact_email=email, entries=entries)

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        if self._token is None:
            self._token = self.login_with_totp()
        return self._token

    def admin(self):
        return self.as_role(ClubRole.Roles.ADMIN)

    def call(self, method, path, token=None, **kwargs):
        return self.api(method, path, token=token, host=CLUB_HOST, **kwargs)

    def stranger_batch(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = datetime.date.today()
        season = Season.objects.create(club=other, start_date=today - datetime.timedelta(days=10), end_date=today + datetime.timedelta(days=200))
        product = Product.objects.create(club=other, name="Reg", product_type=Product.ProductType.MEMBERSHIP, season=season, price=Decimal("50.00"))
        variant = ProductVariant.objects.create(product=product, name="Std", price=Decimal("50.00"))
        return self.make_batch("other@example.com", [("Sam", "Stranger")], club=other, variant=variant)

    def line_id(self, batch=None):
        return str((batch or self.batch).entries.get().pk)

    def confirm(self, batch=None, token=None, **body):
        return self.call("post", f"{BASE}/{(batch or self.batch).pk}/confirm", token or self.admin(), data=body)


class AccessTests(RegistrationsTestCase):
    def endpoints(self, pk):
        return (
            ("get", BASE, None),
            ("get", f"{BASE}/{pk}", None),
            ("post", f"{BASE}/{pk}/confirm", {}),
            ("post", f"{BASE}/{pk}/cancel", None),
            ("post", f"{BASE}/{pk}/mark-paid", None),
            ("get", f"{BASE}/{pk}/invoice/pdf", None),
        )

    def test_no_token_is_401(self):
        for method, path, data in self.endpoints(self.batch.pk):
            self.assertError(self.call(method, path, data=data), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        token = self.login()
        for method, path, data in self.endpoints(self.batch.pk):
            self.assertError(self.call(method, path, token, data=data), 403, "forbidden")

    def test_member_admin_is_403(self):
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        for method, path, data in self.endpoints(self.batch.pk):
            self.assertError(self.call(method, path, token, data=data), 403, "forbidden")
        self.batch.refresh_from_db()
        self.assertIsNone(self.batch.invoice_sent_at)

    def test_other_clubs_registration_is_404(self):
        other = self.stranger_batch()
        token = self.admin()
        for method, path, data in self.endpoints(other.pk)[1:]:
            self.assertError(self.call(method, path, token, data=data), 404, "not_found")
        self.assertIsNone(RegistrationBatch.objects.get(pk=other.pk).invoice_sent_at)

    def test_unknown_id_is_404(self):
        token = self.admin()
        for method, path, data in self.endpoints(uuid.uuid4())[1:]:
            self.assertError(self.call(method, path, token, data=data), 404, "not_found")

    def test_me_lists_the_area_for_admins_only(self):
        areas = {a["key"]: a for a in self.call("get", "/manage/me", self.admin()).json()["areas"]}
        self.assertTrue(areas["registrations"]["can_manage"])


class QueueAndDetailTests(RegistrationsTestCase):
    def test_queue_lists_pending_with_totals(self):
        body = self.call("get", BASE, self.admin()).json()
        self.assertEqual(body["total"], 1)
        row = body["items"][0]
        self.assertEqual((row["id"], row["total"], row["contact_email"]), (str(self.batch.pk), "80.00", "reg-parent@example.com"))
        self.assertEqual(row["lines"][0]["member_name"], "Kid One")

    def test_queue_search_and_other_club_isolation(self):
        self.stranger_batch()
        token = self.admin()
        self.assertEqual(self.call("get", f"{BASE}?q=One", token).json()["total"], 1)
        self.assertEqual(self.call("get", f"{BASE}?q=Nobody", token).json()["total"], 0)
        self.assertEqual(self.call("get", BASE, token).json()["total"], 1)

    def test_confirmed_registration_leaves_the_queue(self):
        token = self.admin()
        self.assertEqual(self.confirm(token=token).status_code, 200)
        self.assertEqual(self.call("get", BASE, token).json()["total"], 0)

    def test_pending_detail_has_lines_and_options(self):
        body = self.call("get", f"{BASE}/{self.batch.pk}", self.admin()).json()
        self.assertEqual((body["status"], body["total"], body["payment_status"], body["invoice_number"]), ("pending", "80.00", None, ""))
        self.assertEqual(body["contact"]["email"], "reg-parent@example.com")
        self.assertEqual(body["lines"][0]["variant_name"], "Standard")
        self.assertEqual([v["id"] for v in body["options"]["variants"]], [str(self.variant.pk)])
        self.assertEqual(body["options"]["variants"][0]["price"], "80.00")

    def test_confirmed_detail_has_no_options_and_a_status(self):
        token = self.admin()
        self.confirm(token=token)
        body = self.call("get", f"{BASE}/{self.batch.pk}", token).json()
        self.assertEqual((body["status"], body["payment_status"], body["all_paid"]), ("confirmed", "Unpaid", False))
        self.assertEqual(body["payment_status_code"], "unpaid")
        self.assertIsNone(body["options"])
        self.assertTrue(body["invoice_number"])


@MAIL
class ConfirmTests(RegistrationsTestCase):
    def test_confirm_numbers_and_emails_the_invoice(self):
        response = self.confirm(due_in_days=7)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["email_sent"])
        self.assertEqual(body["registration"]["status"], "confirmed")
        self.assertEqual(body["registration"]["invoice_due_date"], str(datetime.date.today() + datetime.timedelta(days=7)))
        self.assertEqual(len(mail.outbox), 1)
        self.batch.refresh_from_db()
        self.assertIsNotNone(self.batch.invoice_sent_at)

    def test_edit_price_updates_the_fee_and_total(self):
        response = self.confirm(lines=[{"id": self.line_id(), "price": "60.00"}], manual_discount_amount="10.00", manual_discount_note="Loyalty")
        self.assertEqual(response.status_code, 200, response.content)
        registration = response.json()["registration"]
        self.assertEqual((registration["subtotal"], registration["total"], registration["manual_discount_note"]), ("60.00", "50.00", "Loyalty"))
        membership = self.batch.entries.get().membership
        membership.refresh_from_db()
        self.assertEqual(membership.fee_amount, Decimal("50.00"))

    def test_omitted_fields_keep_their_value(self):
        team = Team.objects.create(club=self.club, name="U10", short_name="U10")
        self.confirm(lines=[{"id": self.line_id(), "requested_team_id": str(team.pk)}])
        entry = self.batch.entries.get()
        self.assertEqual((entry.price, entry.requested_team_id, entry.excluded_from_invoice), (Decimal("80.00"), team.pk, False))

    def test_confirm_places_a_player_on_the_requested_team(self):
        team = Team.objects.create(club=self.club, name="U10", short_name="U10")
        response = self.confirm(lines=[{"id": self.line_id(), "requested_team_id": str(team.pk)}])
        self.assertEqual(response.status_code, 200, response.content)
        entry = self.batch.entries.get()
        entry.refresh_from_db()
        self.assertTrue(TeamMembership.objects.filter(team=team, member=entry.membership.member, season=self.season).exists())
        self.assertIsNotNone(entry.resulting_team_membership_id)

    def test_confirm_places_a_volunteer_as_staff(self):
        team = Team.objects.create(club=self.club, name="U10", short_name="U10")
        position = Position.objects.create(club=self.club, name="Trainer", short_name="TR", staff_position=True)
        entry = self.batch.entries.get()
        entry.entry_kind = "volunteer"
        entry.save()
        response = self.confirm(lines=[{"id": str(entry.pk), "requested_team_id": str(team.pk), "requested_position_id": str(position.pk)}])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(StaffAssignment.objects.filter(team=team, member=entry.membership.member, position=position).exists())

    def test_jersey_number_must_be_free(self):
        pool = NumberPool.objects.create(club=self.club, name="Pool", min_number=1, max_number=5)
        team = Team.objects.create(club=self.club, name="U10", short_name="U10", pool=pool)
        response = self.confirm(lines=[{"id": self.line_id(), "requested_team_id": str(team.pk), "requested_jersey_number": 99}])
        self.assertEqual(response.status_code, 422, response.content)
        self.assertIn("lines.0.requested_jersey_number", response.json()["error"]["fields"])
        self.batch.refresh_from_db()
        self.assertIsNone(self.batch.invoice_sent_at)

    def test_variant_of_another_club_is_422(self):
        other = self.stranger_batch()
        foreign = other.entries.get().product_variant
        response = self.confirm(lines=[{"id": self.line_id(), "product_variant_id": str(foreign.pk)}])
        self.assertEqual(response.status_code, 422, response.content)
        self.assertIn("lines.0.product_variant_id", response.json()["error"]["fields"])

    def test_line_of_another_registration_is_422(self):
        other = self.make_batch("second@example.com", [("Kid", "Two")])
        response = self.confirm(lines=[{"id": self.line_id(other), "price": "1.00"}])
        self.assertEqual(response.status_code, 422, response.content)
        self.assertIn("lines", response.json()["error"]["fields"])

    def test_duplicate_line_is_422(self):
        line = self.line_id()
        self.assertEqual(self.confirm(lines=[{"id": line}, {"id": line}]).status_code, 422)

    def test_invalid_discount_and_due_days_are_422(self):
        token = self.admin()
        self.assertEqual(self.confirm(token=token, manual_discount_amount="-1").status_code, 422)
        self.assertEqual(self.confirm(token=token, due_in_days=0).status_code, 422)

    def test_excluding_every_line_confirms_nothing(self):
        response = self.confirm(lines=[{"id": self.line_id(), "excluded_from_invoice": True}])
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(response.json()["error"]["code"], "nothing_to_invoice")
        self.batch.refresh_from_db()
        self.assertIsNone(self.batch.invoice_sent_at)
        self.assertTrue(self.batch.entries.get().excluded_from_invoice)

    def test_confirming_twice_is_404(self):
        token = self.admin()
        self.assertEqual(self.confirm(token=token).status_code, 200)
        self.assertError(self.confirm(token=token), 404, "not_found")

    def test_idempotency_key_replays_the_first_answer(self):
        token = self.admin()
        first = self.call("post", f"{BASE}/{self.batch.pk}/confirm", token, data={}, HTTP_IDEMPOTENCY_KEY="k-1")
        second = self.call("post", f"{BASE}/{self.batch.pk}/confirm", token, data={}, HTTP_IDEMPOTENCY_KEY="k-1")
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(first.json(), second.json())
        self.assertEqual(len(mail.outbox), 1)


class CancelAndPayTests(RegistrationsTestCase):
    def test_cancel_withdraws_every_person(self):
        batch = self.make_batch("fam@example.com", [("Kid", "Two"), ("Kid", "Three")])
        response = self.call("post", f"{BASE}/{batch.pk}/cancel", self.admin())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"cancelled": 2})
        # Two people new to the club: cancelling removes them outright (club.services.cancellation).
        self.assertFalse(ClubMembership.objects.filter(member__first_name="Kid", member__last_name__in=["Two", "Three"]).exists())
        self.assertEqual(self.call("get", BASE, self.admin()).json()["total"], 1)

    def test_cancel_a_confirmed_registration_is_404(self):
        token = self.admin()
        self.confirm(token=token)
        self.assertError(self.call("post", f"{BASE}/{self.batch.pk}/cancel", token), 404, "not_found")

    @MAIL
    def test_mark_paid_settles_everyone(self):
        token = self.admin()
        self.confirm(token=token)
        response = self.call("post", f"{BASE}/{self.batch.pk}/mark-paid", token)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["payment_status"], body["all_paid"]), ("Paid", True))
        self.assertEqual(body["payment_status_code"], "paid")
        membership = self.batch.entries.get().membership
        membership.refresh_from_db()
        self.assertEqual(membership.fee_status, ClubMembership.FeeStatus.PAID)

    def test_mark_paid_while_pending_is_404(self):
        self.assertError(self.call("post", f"{BASE}/{self.batch.pk}/mark-paid", self.admin()), 404, "not_found")


class PdfTests(RegistrationsTestCase):
    def test_pdf_download(self):
        with mock.patch("api.panel.routers.registrations.batch_invoice_pdf", return_value=b"%PDF-fake"):
            response = self.call("get", f"{BASE}/{self.batch.pk}/invoice/pdf", self.admin())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(f"{self.club.slug}-registration-{self.batch.pk}.pdf", response["Content-Disposition"])
        self.assertEqual(response.content, b"%PDF-fake")

    def test_pdf_unavailable_is_503(self):
        with mock.patch("api.panel.routers.registrations.batch_invoice_pdf", side_effect=RegistrationInvoicePDFError("no pango")):
            self.assertError(self.call("get", f"{BASE}/{self.batch.pk}/invoice/pdf", self.admin()), 503, "pdf_unavailable")
