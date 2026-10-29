import datetime
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import override_settings

from api.models import Job
from api.protected.tests import CLUB_HOST, AppApiTestCase
from club.models import Club, ClubMembership, ClubRole, DuesInvoice, FeePayment, Season
from club.services.invoicing import DuesInvoicePDFError
from management.pdf import PDFExportError
from members.models import Member

BASE = "/manage/members/memberships"
EAGER = override_settings(CELERY_TASK_ALWAYS_EAGER=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")


class FeesTestCase(AppApiTestCase):
    def setUp(self):
        super().setUp()
        self.kim = ClubMembership.objects.get(member=self.child, season=self.season)
        self.kim.fee_amount = Decimal("100.00")
        self.kim.save()

    def as_role(self, role):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        return self.login_with_totp()

    def admin(self):
        return self.as_role(ClubRole.Roles.ADMIN)

    def call(self, method, path, token=None, **kwargs):
        return self.api(method, path, token=token, host=CLUB_HOST, **kwargs)

    def stranger_membership(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        today = datetime.date.today()
        season = Season.objects.create(club=other, start_date=today - datetime.timedelta(days=10), end_date=today + datetime.timedelta(days=200))
        stranger = Member.objects.create(first_name="Sam", last_name="Stranger", email="sam@example.com")
        return ClubMembership.objects.create(club=other, member=stranger, season=season, status="active", fee_amount=Decimal("50.00"))

    def invoice_for_kim(self):
        return DuesInvoice.objects.create(club=self.club, membership=self.kim, amount=Decimal("100.00"), due_date=datetime.date.today() - datetime.timedelta(days=3), sent_at=self.kim.created, sent_to_email="parent@example.com", sent_to_guardian=True)


class AccessTests(FeesTestCase):
    def endpoints(self, pk):
        return (
            ("get", f"{BASE}/summary", None),
            ("get", f"{BASE}/export", None),
            ("post", f"{BASE}/mark-paid", {"membership_ids": [str(pk)]}),
            ("post", f"{BASE}/{pk}/mark-fully-paid", None),
            ("post", f"{BASE}/{pk}/record-payment", {"amount": "10.00"}),
            ("get", f"{BASE}/{pk}/invoice", None),
            ("get", f"{BASE}/{pk}/invoice/pdf", None),
            ("post", f"{BASE}/send-invoices", {"membership_ids": [str(pk)]}),
            ("post", f"{BASE}/send-invoice-reminders", None),
        )

    def test_no_token_is_401(self):
        for method, path, data in self.endpoints(self.kim.pk) + (("get", "/manage/invoices", None),):
            self.assertError(self.call(method, path, data=data), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        token = self.login()
        for method, path, data in self.endpoints(self.kim.pk) + (("get", "/manage/invoices", None),):
            self.assertError(self.call(method, path, token, data=data), 403, "forbidden")

    def test_member_admin_is_403(self):
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        for method, path, data in self.endpoints(self.kim.pk):
            self.assertError(self.call(method, path, token, data=data), 403, "forbidden")
        self.assertEqual(FeePayment.objects.count(), 0)

    def test_other_clubs_membership_is_404(self):
        other = self.stranger_membership()
        token = self.admin()
        for method, path, data in self.endpoints(other.pk):
            if path.endswith(("summary", "export", "send-invoices", "send-invoice-reminders", "/mark-paid")):
                continue  # not addressed at one membership (bulk ids of other clubs are ignored, see below)
            self.assertError(self.call(method, path, token, data=data), 404, "not_found")
        other.refresh_from_db()
        self.assertEqual(other.amount_paid, 0)

    def test_bulk_ignores_other_clubs_ids(self):
        other = self.stranger_membership()
        token = self.admin()
        response = self.call("post", f"{BASE}/mark-paid", token, data={"membership_ids": [str(other.pk)]})
        self.assertEqual(response.json(), {"updated": 0})
        other.refresh_from_db()
        self.assertEqual(other.fee_status, "unpaid")


class SummaryTests(FeesTestCase):
    def test_summary_counts(self):
        ClubMembership.objects.filter(pk=self.kim.pk).update(fee_status="paid")
        other = Member.objects.create(first_name="Lee", last_name="Late")
        ClubMembership.objects.create(club=self.club, member=other, season=self.season, status="active", fee_status="unpaid")
        gone = Member.objects.create(first_name="Gus", last_name="Gone")
        ClubMembership.objects.create(club=self.club, member=gone, season=self.season, status="cancelled", fee_status="unpaid")
        body = self.call("get", f"{BASE}/summary", self.admin()).json()
        self.assertEqual(body["season_id"], str(self.season.pk))
        self.assertEqual((body["total"], body["paid"], body["unpaid"], body["partial"], body["waived"]), (2, 1, 1, 0, 0))
        self.assertEqual(body["paid_rate"], 50)
        self.assertEqual(body["overdue_invoices"], 0)

    def test_summary_counts_overdue_invoices(self):
        self.invoice_for_kim()
        self.assertEqual(self.call("get", f"{BASE}/summary", self.admin()).json()["overdue_invoices"], 1)


class PaymentTests(FeesTestCase):
    def test_mark_paid_bulk(self):
        token = self.admin()
        response = self.call("post", f"{BASE}/mark-paid", token, data={"membership_ids": [str(self.kim.pk)]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"updated": 1})
        self.kim.refresh_from_db()
        self.assertEqual((self.kim.fee_status, self.kim.amount_paid), ("paid", Decimal("100.00")))
        self.assertEqual(self.kim.status, "active")

    def test_mark_paid_needs_ids(self):
        self.assertEqual(self.call("post", f"{BASE}/mark-paid", self.admin(), data={"membership_ids": []}).status_code, 422)

    def test_mark_fully_paid(self):
        response = self.call("post", f"{BASE}/{self.kim.pk}/mark-fully-paid", self.admin())
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["fee_status"], body["remaining_balance"], body["first_name"]), ("paid", "0.00", "Kim"))

    def test_record_partial_then_full(self):
        token = self.admin()
        response = self.call("post", f"{BASE}/{self.kim.pk}/record-payment", token, data={"amount": "40.00", "method": "cash", "reference": "R1"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["payment"]["amount"], body["payment"]["method"], body["membership"]["fee_status"], body["membership"]["remaining_balance"]), ("40.00", "cash", "partially_paid", "60.00"))
        response = self.call("post", f"{BASE}/{self.kim.pk}/record-payment", token, data={"amount": "60.00"})
        self.assertEqual(response.json()["membership"]["fee_status"], "paid")
        self.assertEqual(FeePayment.objects.filter(membership=self.kim).count(), 2)

    def test_record_payment_validation(self):
        token = self.admin()
        for data in ({"amount": "0"}, {"amount": "-5"}, {"amount": "10", "method": "bitcoin"}, {}):
            response = self.call("post", f"{BASE}/{self.kim.pk}/record-payment", token, data=data)
            self.assertEqual(response.status_code, 422, (data, response.content))
        self.assertEqual(FeePayment.objects.count(), 0)

    def test_record_payment_idempotent(self):
        token = self.admin()
        for _i in range(2):
            response = self.call("post", f"{BASE}/{self.kim.pk}/record-payment", token, data={"amount": "25.00"}, HTTP_IDEMPOTENCY_KEY="pay-1")
            self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(FeePayment.objects.filter(membership=self.kim).count(), 1)


class InvoiceTests(FeesTestCase):
    def test_detail(self):
        invoice = self.invoice_for_kim()
        response = self.call("get", f"{BASE}/{self.kim.pk}/invoice", self.admin())
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["number"], body["amount"], body["is_overdue"], body["is_paid"], body["sent_to_guardian"]), (invoice.number, "100.00", True, False, True))
        self.assertEqual(body["membership"]["id"], str(self.kim.pk))

    def test_detail_without_invoice_is_404(self):
        self.assertError(self.call("get", f"{BASE}/{self.kim.pk}/invoice", self.admin()), 404, "not_found")

    def test_pdf_download(self):
        invoice = self.invoice_for_kim()
        with mock.patch("api.panel.routers.fees.invoice_pdf", return_value=b"%PDF-fake"):
            response = self.call("get", f"{BASE}/{self.kim.pk}/invoice/pdf", self.admin())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(f"{invoice.number}.pdf", response["Content-Disposition"])
        self.assertEqual(response.content, b"%PDF-fake")

    def test_pdf_unavailable_is_503(self):
        self.invoice_for_kim()
        with mock.patch("api.panel.routers.fees.invoice_pdf", side_effect=DuesInvoicePDFError("no pango")):
            self.assertError(self.call("get", f"{BASE}/{self.kim.pk}/invoice/pdf", self.admin()), 503, "pdf_unavailable")

    @EAGER
    def test_send_invoices_job(self):
        token = self.admin()
        with self.captureOnCommitCallbacks(execute=True), mock.patch("club.services.invoicing.invoice_pdf", side_effect=DuesInvoicePDFError("x")):
            response = self.call("post", f"{BASE}/send-invoices", token, data={"membership_ids": [str(self.kim.pk)], "due_in_days": 7})
        self.assertEqual(response.status_code, 202, response.content)
        job = self.call("get", f"/manage/jobs/{response.json()['id']}", token).json()
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(job["result"], {"sent": 1, "failed": 0, "unreachable": 0})
        invoice = DuesInvoice.objects.get(membership=self.kim)
        self.assertEqual((invoice.sent_to_email, invoice.sent_to_guardian, invoice.due_date), ("parent@example.com", True, datetime.date.today() + datetime.timedelta(days=7)))
        self.assertEqual(len(mail.outbox), 1)

    @EAGER
    def test_send_invoices_unreachable(self):
        nobody = Member.objects.create(first_name="Ned", last_name="Nomail")
        membership = ClubMembership.objects.create(club=self.club, member=nobody, season=self.season, status="active", fee_amount=Decimal("20.00"))
        token = self.admin()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.call("post", f"{BASE}/send-invoices", token, data={"membership_ids": [str(membership.pk)]})
        job = self.call("get", f"/manage/jobs/{response.json()['id']}", token).json()
        self.assertEqual(job["result"], {"sent": 0, "failed": 0, "unreachable": 1})
        self.assertFalse(DuesInvoice.objects.filter(membership=membership).exists())

    def test_send_invoices_validation(self):
        token = self.admin()
        for data in ({"membership_ids": []}, {"membership_ids": [str(self.kim.pk)], "due_in_days": 0}, {"membership_ids": [str(self.kim.pk)], "due_in_days": 400}, {"membership_ids": ["nope"]}):
            self.assertEqual(self.call("post", f"{BASE}/send-invoices", token, data=data).status_code, 422, data)
        self.assertEqual(Job.objects.count(), 0)

    @EAGER
    def test_send_invoices_idempotent(self):
        token = self.admin()
        ids = []
        for _i in range(2):
            with self.captureOnCommitCallbacks(execute=True), mock.patch("club.services.invoicing.invoice_pdf", side_effect=DuesInvoicePDFError("x")):
                ids.append(self.call("post", f"{BASE}/send-invoices", token, data={"membership_ids": [str(self.kim.pk)]}, HTTP_IDEMPOTENCY_KEY="send-1").json()["id"])
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(Job.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    @EAGER
    def test_reminders_job(self):
        invoice = self.invoice_for_kim()
        token = self.admin()
        with self.captureOnCommitCallbacks(execute=True), mock.patch("club.services.invoicing.invoice_pdf", side_effect=DuesInvoicePDFError("x")):
            response = self.call("post", f"{BASE}/send-invoice-reminders", token)
        self.assertEqual(response.status_code, 202, response.content)
        job = self.call("get", f"/manage/jobs/{response.json()['id']}", token).json()
        self.assertEqual((job["status"], job["result"]), ("succeeded", {"sent": 1, "failed": 0}))
        invoice.refresh_from_db()
        self.assertEqual(invoice.reminder_count, 1)

    @EAGER
    def test_reminders_nothing_due(self):
        token = self.admin()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.call("post", f"{BASE}/send-invoice-reminders", token)
        self.assertEqual(self.call("get", f"/manage/jobs/{response.json()['id']}", token).json()["result"], {"sent": 0, "failed": 0})


class ExportTests(FeesTestCase):
    def test_export_pdf_uses_filtered_list(self):
        with mock.patch("api.panel.routers.fees.membership_list_pdf", return_value=b"%PDF-list") as pdf:
            response = self.call("get", f"{BASE}/export?fee_status=all&q=kim", self.admin())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        context = pdf.call_args.args[0]
        self.assertEqual([m.member.first_name for m in context["memberships"]], ["Kim"])
        self.assertEqual(context["selected_season"], self.season)
        self.assertEqual(context["kpi_total"], 1)

    def test_pdf_template_renders_with_service_context(self):
        # WeasyPrint may be missing on this machine: render the HTML itself so the template/context pair is checked.
        from django.template.loader import render_to_string

        captured = {}

        def fake(context):
            captured["html"] = render_to_string("management/membership_list_pdf.html", context)
            return b"%PDF"

        with mock.patch("api.panel.routers.fees.membership_list_pdf", side_effect=fake):
            self.call("get", f"{BASE}/export?fee_status=all", self.admin())
        self.assertIn("Kim", captured["html"])

    def test_export_pdf_unavailable_is_503(self):
        with mock.patch("api.panel.routers.fees.membership_list_pdf", side_effect=PDFExportError("no pango")):
            self.assertError(self.call("get", f"{BASE}/export", self.admin()), 503, "pdf_unavailable")

    def test_export_other_clubs_season_is_404(self):
        other = self.stranger_membership()
        self.assertError(self.call("get", f"{BASE}/export?season_id={other.season_id}", self.admin()), 404, "not_found")


class InvoiceListTests(FeesTestCase):
    def test_lists_dues_invoices_newest_first(self):
        invoice = self.invoice_for_kim()
        body = self.call("get", "/manage/invoices", self.admin()).json()
        self.assertEqual(body["total"], 1)
        row = body["items"][0]
        self.assertEqual((row["kind"], row["number"], row["status"], row["ref_id"], row["billed_to_member_id"]), ("dues", invoice.number, "Overdue", str(self.kim.pk), str(self.child.pk)))
        self.assertEqual(body["kinds"], ["order", "dues", "registration"])

    def test_filters_and_search(self):
        self.invoice_for_kim()
        token = self.admin()
        self.assertEqual(self.call("get", "/manage/invoices?type=order", token).json()["total"], 0)
        self.assertEqual(self.call("get", "/manage/invoices?type=dues&q=kim", token).json()["total"], 1)
        self.assertEqual(self.call("get", "/manage/invoices?q=zzz", token).json()["total"], 0)
        self.assertEqual(self.call("get", "/manage/invoices?type=bogus", token).status_code, 422)

    def test_paginates(self):
        self.invoice_for_kim()
        for i in range(2):
            member = Member.objects.create(first_name=f"P{i}", last_name="Zed")
            membership = ClubMembership.objects.create(club=self.club, member=member, season=self.season, status="active", fee_amount=Decimal("10.00"))
            DuesInvoice.objects.create(club=self.club, membership=membership, amount=Decimal("10.00"), due_date=datetime.date.today(), sent_at=membership.created)
        token = self.admin()
        first = self.call("get", "/manage/invoices?limit=2", token).json()
        self.assertEqual((len(first["items"]), first["total"]), (2, 3))
        second = self.call("get", f"/manage/invoices?limit=2&cursor={first['next_cursor']}", token).json()
        self.assertEqual(len(second["items"]), 1)
        self.assertIsNone(second["next_cursor"])
        self.assertError(self.call("get", "/manage/invoices?cursor=garbage", token), 400, "invalid_cursor")

    def test_does_not_leak_other_club(self):
        other = self.stranger_membership()
        DuesInvoice.objects.create(club=other.club, membership=other, amount=Decimal("50.00"), due_date=datetime.date.today(), sent_at=other.created)
        self.assertEqual(self.call("get", "/manage/invoices", self.admin()).json()["total"], 0)

    def test_shop_manager_gets_orders_only(self):
        from club.models import ShopManager

        self.invoice_for_kim()
        ShopManager.objects.create(club=self.club, member=self.member)
        token = self.login_with_totp()
        with mock.patch("api.panel.routers.fees.club_features", return_value={"shop": True}):
            body = self.call("get", "/manage/invoices?type=dues", token).json()
        self.assertEqual((body["total"], body["kinds"]), (0, ["order"]))
