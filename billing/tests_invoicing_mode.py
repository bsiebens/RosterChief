"""Automatic invoicing on/off: the daily renewal job, the warnings (weekly digest + first-time alert) and the manual open-period /
create-invoice flow with a typed number."""

import datetime
from unittest import mock

from django.core import mail
from django.core.cache import cache

from controlpanel.models import EmailSuppression, PlatformAuditLog

from .models import BillingSettings, ClubBilling, Due, Invoice, InvoiceSequence, InvoicingAlert
from .services import dues as dues_svc
from .services import invoicing_mode
from .tasks import renew_subscriptions, send_invoicing_alerts, send_invoicing_digest
from .tests import START, ApiBase, at, counts

D = datetime.date
# START is 2026-01-01: the first period ends 2026-12-31, the next one starts 2027-01-01 and (30 lead days) its window opens on 2026-12-02.
OPENS = D(2026, 12, 2)


def set_auto(value: bool):
    row = BillingSettings.current()
    row.auto_invoicing = value
    row.save()


class ModeBase(ApiBase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(invoicing_mode, "recipients", return_value=["admin1@example.com", "admin2@example.com"])
        patcher.start()
        self.addCleanup(patcher.stop)
        mail.outbox.clear()

    def pause_email(self, on: bool):
        sw = EmailSuppression.current()
        sw.is_active = on
        sw.save()
        cache.delete(EmailSuppression._cache_key())
        self.addCleanup(cache.delete, EmailSuppression._cache_key())


class RenewalSwitchTests(ModeBase):
    def test_on_is_exactly_today_period_invoice_and_automatic_number(self):
        club, _due = self.billed("On")
        self.assertTrue(BillingSettings.current().auto_invoicing)  # the default
        with at(OPENS + datetime.timedelta(days=3)):
            result = renew_subscriptions()
        self.assertIn("1 done", result)
        new = Due.objects.get(club=club, period_start=D(2027, 1, 1))
        self.assertEqual((new.invoice.number, new.invoice.custom_number), ("INV-2026-00002", False))

    def test_off_opens_nothing_and_takes_no_number(self):
        club, _due = self.billed("Off")
        set_auto(False)
        before = counts()
        with at(OPENS + datetime.timedelta(days=3)):
            result = renew_subscriptions()
        self.assertIn("skipped", result)
        self.assertEqual(counts(), before)
        self.assertFalse(Due.objects.filter(club=club, period_start=D(2027, 1, 1)).exists())
        self.assertEqual(InvoiceSequence.objects.get().last_number, 1)

    def test_dry_run_is_unchanged_while_on(self):
        self.billed("Dry")
        with at(OPENS + datetime.timedelta(days=3)):
            self.assertEqual(renew_subscriptions(dry_run=True), "renew_subscriptions: 1 done")
        self.assertEqual(Due.objects.count(), 1)


class WindowTests(ModeBase):
    def test_window_maths_including_the_next_seven_days(self):
        club, _due = self.billed("Win")
        self.assertEqual(invoicing_mode.needs_invoice(OPENS - datetime.timedelta(days=8), 7), [])  # opens in 8 days: not yet
        (row,) = invoicing_mode.needs_invoice(OPENS - datetime.timedelta(days=7), 7)  # opens in exactly 7 days
        self.assertEqual((row["kind"], row["club"], row["period_start"], row["opens_on"], row["days"], row["amount"]), ("open", club, D(2027, 1, 1), OPENS, 7, "121.00"))
        self.assertEqual(invoicing_mode.needs_invoice(OPENS - datetime.timedelta(days=1), 0), [])
        (row,) = invoicing_mode.needs_invoice(OPENS, 0)
        self.assertEqual(row["days"], 0)
        (row,) = invoicing_mode.needs_invoice(OPENS + datetime.timedelta(days=10), 0)
        self.assertEqual(row["days"], -10)  # the window opened 10 days ago

    def test_opened_and_invoiced_or_archived_clubs_are_not_listed(self):
        club, _due = self.billed("Done")
        dues_svc.open_period(club, start=D(2027, 1, 1))  # next period opened and invoiced
        gone, _ = self.billed("Gone")
        gone.archived_at = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
        gone.save()
        with at(OPENS):
            self.assertEqual(invoicing_mode.needs_invoice(OPENS, 7), [])

    def test_a_period_opened_without_invoice_is_listed_until_it_is_invoiced(self):
        club, _due = self.billed("Half")
        due = dues_svc.open_period(club, start=D(2027, 1, 1), invoice=False)
        (row,) = invoicing_mode.needs_invoice(OPENS, 0)
        self.assertEqual((row["kind"], row["due"]), ("invoice", due))
        dues_svc.issue_period_invoice(due, number="EXT-1")
        self.assertEqual(invoicing_mode.needs_invoice(OPENS, 0), [])


class DigestAndAlertTests(ModeBase):
    def test_nothing_is_sent_while_automatic_invoicing_is_on(self):
        self.billed("Quiet")
        with at(OPENS):
            self.assertIn("skipped", send_invoicing_digest())
            self.assertIn("skipped", send_invoicing_alerts())
        self.assertEqual(mail.outbox, [])
        self.assertEqual(InvoicingAlert.objects.count(), 0)

    def test_digest_goes_to_all_admins_and_lists_the_clubs(self):
        self.billed("Alpha Club")
        self.billed("Beta Club")
        set_auto(False)
        with at(OPENS - datetime.timedelta(days=5)):
            self.assertEqual(invoicing_mode.send_digest(), 2)
        (msg,) = mail.outbox
        self.assertEqual(msg.to, ["admin1@example.com", "admin2@example.com"])
        self.assertIn("2 club(s)", msg.subject)
        for needle in ("Alpha Club", "Beta Club", "period from 2027-01-01", "window opens in 5 day(s)", "EUR 121.00"):
            self.assertIn(needle, msg.body)
        self.assertIn("Alpha Club", msg.alternatives[0][0])

    def test_empty_digest_sends_no_mail(self):
        self.billed("Early")
        set_auto(False)
        with at(D(2026, 3, 1)):
            self.assertEqual(invoicing_mode.send_digest(), 0)
            self.assertIn("0 club(s)", send_invoicing_digest())
        self.assertEqual(mail.outbox, [])

    def test_first_time_alert_is_sent_once_per_club_and_period(self):
        club, _due = self.billed("Alert Club")
        set_auto(False)
        with at(OPENS - datetime.timedelta(days=1)):
            self.assertEqual(invoicing_mode.send_window_alerts(), 0)  # the window has not opened
        with at(OPENS):
            self.assertEqual(invoicing_mode.send_window_alerts(), 1)
            self.assertEqual(len(mail.outbox), 1)
            self.assertIn("Alert Club", mail.outbox[0].body)
            self.assertIn("manually", mail.outbox[0].body)
        self.assertEqual(InvoicingAlert.objects.filter(club=club, period_start=D(2027, 1, 1)).count(), 1)
        with at(OPENS + datetime.timedelta(days=1)):
            self.assertEqual(invoicing_mode.send_window_alerts(), 0)  # same club and period: silent
        self.assertEqual(len(mail.outbox), 1)

    def test_alert_respects_the_email_pause_and_is_retried_afterwards(self):
        club, _due = self.billed("Paused Club")
        set_auto(False)
        self.pause_email(True)
        with at(OPENS):
            invoicing_mode.send_window_alerts()
        self.assertEqual(mail.outbox, [])
        self.assertFalse(InvoicingAlert.objects.exists())  # not recorded: it will be tried again
        self.pause_email(False)
        with at(OPENS + datetime.timedelta(days=1)):
            self.assertEqual(invoicing_mode.send_window_alerts(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(InvoicingAlert.objects.filter(club=club).count(), 1)


class ManualFlowApiTests(ModeBase):
    def open_without_invoice(self, club, start="2027-01-01"):
        return self.post(f"/clubs/{club.pk}/billing/periods", {"start": start, "invoice": False})

    def test_open_the_period_without_an_invoice(self):
        club, _due = self.billed("Manual")
        before = counts()
        r = self.open_without_invoice(club)
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertIsNone(body["invoice"])
        self.assertEqual((counts()["due"], counts()["invoice"], counts()["invoice_seq"]), (before["due"] + 1, before["invoice"], before["invoice_seq"]))
        again = self.open_without_invoice(club)  # the idempotency guard on period_start
        self.assertEqual((again.status_code, again.json()["error"]["code"]), (409, "billing_error"))
        log = PlatformAuditLog.objects.get(action="billing.period_opened", target_id=body["id"])
        self.assertEqual(log.data, {"without_invoice": True})

    def test_a_number_cannot_be_combined_with_opening_without_invoice(self):
        club, _due = self.billed("Combo")
        before = counts()
        r = self.post(f"/clubs/{club.pk}/billing/periods", {"start": "2027-01-01", "invoice": False, "invoice_number": "X-1"})
        self.assertEqual(r.status_code, 422, r.content)
        self.assertEqual(counts(), before)

    def test_create_the_invoice_with_a_typed_number(self):
        club, _due = self.billed("Typed")
        set_auto(False)
        new = Due.objects.get(pk=self.open_without_invoice(club).json()["id"])
        before = counts()
        missing = self.post(f"/billing/dues/{new.pk}/invoice", {})
        self.assertEqual((missing.status_code, list(missing.json()["error"]["fields"])), (422, ["number"]))  # required while automatic invoicing is off
        self.assertEqual(counts(), before)
        for bad in ("   ", "X" * 33, "INV-2026-00077"):
            r = self.post(f"/billing/dues/{new.pk}/invoice", {"number": bad})
            self.assertEqual(r.status_code, 422, bad)
        self.assertEqual(counts(), before)
        done = self.post(f"/billing/dues/{new.pk}/invoice", {"number": "  FAC-2027-7 "})
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(done.json()["number"], "FAC-2027-7")
        inv = Invoice.objects.get(due=new)
        self.assertEqual((inv.number, inv.custom_number), ("FAC-2027-7", True))
        self.assertEqual(InvoiceSequence.objects.get().last_number, 1)  # the automatic counter was never touched
        log = PlatformAuditLog.objects.get(action="billing.invoice_issued", target_id=new.pk)
        self.assertEqual(log.data, {"number": "FAC-2027-7", "custom_number": True})
        self.assertEqual(self.post(f"/billing/dues/{new.pk}/invoice", {"number": "OTHER-1"}).json()["number"], "FAC-2027-7")  # idempotent

    def test_a_duplicate_number_is_a_422_duplicate_number(self):
        club, _due = self.billed("Dup")
        other, _ = self.billed("Other")
        set_auto(False)
        invoicing_taken = self.post(f"/clubs/{other.pk}/billing/periods", {"start": "2027-01-01", "invoice": False}).json()["id"]
        self.post(f"/billing/dues/{invoicing_taken}/invoice", {"number": "TAKEN-1"})
        new = self.open_without_invoice(club).json()["id"]
        before = counts()
        r = self.post(f"/billing/dues/{new}/invoice", {"number": "TAKEN-1"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "duplicate_number"))
        self.assertEqual(counts(), before)

    def test_with_automatic_invoicing_on_the_number_stays_optional(self):
        club, _due = self.billed("Optional")
        new = self.open_without_invoice(club).json()["id"]  # an exceptional manual period
        r = self.post(f"/billing/dues/{new}/invoice", {})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["number"], "INV-2026-00002")  # the next automatic number, as before

    def test_the_switch_is_audited_and_an_omitted_value_leaves_it_alone(self):
        payload = self.get("/billing/settings").json()
        self.assertTrue(payload["auto_invoicing"])
        r = self.client.put("/billing/settings", json={**payload, "auto_invoicing": False})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(BillingSettings.current().auto_invoicing)
        self.assertTrue(PlatformAuditLog.objects.filter(action="billing.auto_invoicing_off").exists())
        payload = {k: v for k, v in self.get("/billing/settings").json().items() if k != "auto_invoicing"}
        r = self.client.put("/billing/settings", json=payload)  # an older client that does not know the field
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(BillingSettings.current().auto_invoicing)
        r = self.client.put("/billing/settings", json={**payload, "auto_invoicing": True})
        self.assertTrue(BillingSettings.current().auto_invoicing)
        self.assertTrue(PlatformAuditLog.objects.filter(action="billing.auto_invoicing_on").exists())

    def test_the_dedicated_switch_endpoint_changes_only_the_switch(self):
        before = self.get("/billing/settings").json()
        r = self.client.put("/billing/settings/auto-invoicing", json={"enabled": False})
        self.assertEqual(r.status_code, 200, r.content)
        after = self.get("/billing/settings").json()
        self.assertEqual((after["auto_invoicing"], after["legal_name"], after["vat_rate"]), (False, before["legal_name"], before["vat_rate"]))
        self.assertEqual(PlatformAuditLog.objects.filter(action="billing.auto_invoicing_off").count(), 1)
        self.client.put("/billing/settings/auto-invoicing", json={"enabled": False})  # no change, no second audit entry
        self.assertEqual(PlatformAuditLog.objects.filter(action="billing.auto_invoicing_off").count(), 1)
        self.client.put("/billing/settings/auto-invoicing", json={"enabled": True})
        self.assertTrue(BillingSettings.current().auto_invoicing)
        self.assertEqual(PlatformAuditLog.objects.filter(action="billing.auto_invoicing_on").count(), 1)

    def test_invoicing_overview_and_summary(self):
        self.billed("Overview")
        set_auto(False)
        out = self.get("/billing/invoicing?ahead_days=7", day=OPENS - datetime.timedelta(days=3)).json()
        self.assertEqual((out["auto_invoicing"], out["pending_count"], out["ahead_days"]), (False, 0, 7))
        (item,) = out["items"]
        self.assertEqual((item["kind"], item["club"]["name"], item["period_start"], item["days"], item["amount"]), ("open", "Overview", "2027-01-01", 3, "121.00"))
        out = self.get("/billing/invoicing", day=OPENS).json()
        self.assertEqual((out["pending_count"], len(out["items"])), (1, 1))
        summary = self.get("/billing/summary", day=OPENS).json()
        self.assertEqual((summary["auto_invoicing"], summary["invoices_needed"]), (False, 1))
        set_auto(True)
        self.assertTrue(self.get("/billing/summary", day=OPENS).json()["auto_invoicing"])

    def test_no_club_billing_is_created_or_changed_by_the_checks(self):
        self.billed("Pure")
        before = (counts(), ClubBilling.objects.count())
        set_auto(False)
        with at(OPENS):
            invoicing_mode.needs_invoice(OPENS, 7)
            invoicing_mode.pending_count(OPENS)
        self.assertEqual((counts(), ClubBilling.objects.count()), before)
        self.assertEqual(START.year, 2026)
