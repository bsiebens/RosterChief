import contextlib
import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from club.models import Club
from features import services as feature_services
from features.entitlements import resolve_entitlements
from features.models import Feature, Plan, PlanFeature, Subscription

from .models import BillingSettings, ClubBilling, Due, Invoice, InvoiceSequence, PlanPrice
from .services import BillingError
from .services import dues as dues_svc
from .services import invoices as invoices_svc

START = datetime.date(2026, 1, 1)


def make_club(name, *, vat_id="", country="", billed=True):
    User = get_user_model()
    owner = User.objects.filter(email="owner@example.com").first() or User.objects.create(email="owner@example.com", is_active=True)
    club = Club(owner=owner, name=name, schema_name=name.lower().replace(" ", "_"), vat_id=vat_id, legal_country=country)
    club.auto_create_schema = False  # billing lives in the public schema; no tenant schema is needed
    club.save()
    if billed:
        ClubBilling.objects.create(club=club)
    return club


@contextlib.contextmanager
def at(day):
    """Pretend today is ``day`` (billing reads the date from ``timezone.localdate``)."""
    with mock.patch("django.utils.timezone.localdate", return_value=day):
        yield


class BillingTestBase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        s = BillingSettings.current()
        s.legal_name, s.country, s.vat_id, s.vat_rate = "Issuer BV", "BE", "BE0123456789", Decimal("21.00")
        s.save()
        feature_services.sync_catalog()
        self.plan = Plan.objects.create(code="pro-test", name="Pro")
        PlanFeature.objects.create(plan=self.plan, feature=Feature.objects.get(code="sponsoring"))
        PlanPrice.objects.create(plan=self.plan, active_from=datetime.date(2000, 1, 1), amount=Decimal("100.00"))

    def open(self, club, plan=None, start=START):
        return dues_svc.open_period(club, start=start, plan=plan or self.plan)


class VatTests(BillingTestBase):
    def test_standard_rate(self):
        due = self.open(make_club("Domestic", vat_id="BE999", country="BE"))
        self.assertEqual((due.amount, due.vat_rate, due.vat_amount, due.total, due.reverse_charge), (Decimal("100.00"), Decimal("21.00"), Decimal("21.00"), Decimal("121.00"), False))

    def test_reverse_charge_eu_with_vat_id(self):
        due = self.open(make_club("Dutch", vat_id="NL123B01", country="NL"))
        self.assertTrue(due.reverse_charge)
        self.assertEqual((due.vat_rate, due.vat_amount, due.total), (Decimal("0"), Decimal("0"), Decimal("100.00")))

    def test_no_reverse_charge_without_vat_id_or_outside_eu(self):
        self.assertFalse(self.open(make_club("NoVat", country="NL")).reverse_charge)
        self.assertFalse(self.open(make_club("Swiss", vat_id="CHE1", country="CH"), start=START).reverse_charge)
        self.assertFalse(self.open(make_club("Unknown", vat_id="X1")).reverse_charge)

    def test_zero_amount_shows_the_standard_rate_with_no_vat_and_is_paid(self):
        free = Plan.objects.create(code="free-test", name="Free")
        PlanPrice.objects.create(plan=free, active_from=datetime.date(2000, 1, 1), amount=0)
        due = self.open(make_club("Freebie", country="BE"), plan=free)
        self.assertEqual((due.vat_rate, due.vat_amount, due.total, due.status, due.reverse_charge), (Decimal("21.00"), Decimal("0"), Decimal("0"), Due.PAID, False))

    def test_zero_amount_for_a_reverse_charge_club_is_still_reverse_charge(self):
        free = Plan.objects.create(code="free-rc", name="Free")
        PlanPrice.objects.create(plan=free, active_from=datetime.date(2000, 1, 1), amount=0)
        due = self.open(make_club("Freebie NL", country="NL", vat_id="NL123456789B01"), plan=free)
        self.assertEqual((due.vat_rate, due.vat_amount, due.reverse_charge, due.status), (Decimal("0"), Decimal("0"), True, Due.PAID))

    def test_vat_is_rounded_to_cents(self):
        s = BillingSettings.current()
        s.vat_rate = Decimal("21.00")
        s.save()
        PlanPrice.objects.create(plan=self.plan, active_from=datetime.date(2025, 6, 1), amount=Decimal("33.33"))
        due = self.open(make_club("Rounding", country="BE"), start=datetime.date(2026, 2, 1))
        self.assertEqual(due.vat_amount, Decimal("7.00"))  # 33.33 * 0.21 = 6.9993 (price in force on the start date is 33.33)

    def test_settings_zero_rate_means_no_vat(self):
        s = BillingSettings.current()
        s.vat_rate = Decimal("0")
        s.save()
        self.assertEqual(self.open(make_club("Untaxed", country="BE")).total, Decimal("100.00"))


class SettlementTests(BillingTestBase):
    def test_balance_and_status_use_gross_total(self):
        club = make_club("Settle", country="BE")
        due = self.open(club)
        self.assertEqual(due.balance, Decimal("121.00"))
        dues_svc.record_payment(due, amount=Decimal("100.00"))
        due.refresh_from_db()
        self.assertEqual((due.status, due.balance), (Due.PARTIAL, Decimal("21.00")))
        payment = dues_svc.record_payment(due, amount=Decimal("21.00"))
        due.refresh_from_db()
        self.assertEqual((due.status, due.balance), (Due.PAID, Decimal("0.00")))
        dues_svc.remove_payment(payment)
        due.refresh_from_db()
        self.assertEqual(due.status, Due.PARTIAL)

    def test_overpayment_is_rejected(self):
        due = self.open(make_club("Over", country="BE"))
        with self.assertRaises(BillingError):
            dues_svc.record_payment(due, amount=Decimal("121.01"))

    def test_owed_stat_counts_vat(self):
        from controlpanel.stats import _dues_owed

        self.open(make_club("Owed", country="BE"))
        self.assertEqual(Decimal(_dues_owed(None)), Decimal("121.00"))


class InvoiceTests(BillingTestBase):
    def test_snapshot_is_frozen(self):
        club = make_club("Frozen", vat_id="BE1", country="BE")
        invoice = self.open(club).invoice
        self.assertEqual(invoice.snapshot["issuer"]["legal_name"], "Issuer BV")
        self.assertEqual(invoice.snapshot["total"], "121.00")
        s = BillingSettings.current()
        s.legal_name, s.vat_rate = "Changed BV", Decimal("6.00")
        s.save()
        club.legal_name = "Renamed VZW"
        club.save()
        invoice.refresh_from_db()
        html = invoices_svc.invoice_html(invoice)
        self.assertIn("Issuer BV", html)
        self.assertNotIn("Changed BV", html)
        self.assertNotIn("Renamed VZW", html)
        self.assertIn(invoice.number, html)

    def test_reverse_charge_note_and_snapshot_less_fallback(self):
        invoice = self.open(make_club("Rc", vat_id="NL1", country="NL")).invoice
        html = invoices_svc.invoice_html(invoice)
        self.assertIn("VAT reverse charged", html)
        self.assertIn("BE0123456789", html)
        self.assertIn("NL1", html)
        Invoice.objects.filter(pk=invoice.pk).update(snapshot={})
        invoice.refresh_from_db()
        self.assertIn("Issuer BV", invoices_svc.invoice_html(invoice))

    def test_automatic_numbers_are_sequential(self):
        year = datetime.date.today().year
        a = self.open(make_club("A1", country="BE")).invoice
        b = self.open(make_club("B1", country="BE")).invoice
        self.assertEqual((a.number, b.number), (f"INV-{year}-00001", f"INV-{year}-00002"))
        self.assertFalse(a.custom_number)

    def test_rollback_does_not_burn_a_number(self):
        year = datetime.date.today().year
        club = make_club("Rolled", country="BE")
        due = Due.objects.create(club=club, plan=self.plan, amount=Decimal("100.00"), period_start=START)
        with mock.patch.object(Invoice.objects, "create", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
            invoices_svc.issue_invoice(due)
        self.assertEqual(invoices_svc.issue_invoice(due).number, f"INV-{year}-00001")

    def test_custom_numbers_do_not_touch_the_counter(self):
        year = datetime.date.today().year
        club = make_club("Manual", country="BE")
        d1 = Due.objects.create(club=club, plan=self.plan, amount=Decimal("100.00"), period_start=START)
        inv = invoices_svc.issue_invoice(d1, number="  EXT-2026-9  ")
        self.assertEqual((inv.number, inv.custom_number), ("EXT-2026-9", True))
        self.assertEqual(invoices_svc.issue_invoice(d1, number="OTHER").pk, inv.pk)  # idempotent
        self.assertFalse(InvoiceSequence.objects.exists())
        d2 = Due.objects.create(club=club, plan=self.plan, amount=Decimal("100.00"), period_start=START + datetime.timedelta(days=400))
        self.assertEqual(invoices_svc.issue_invoice(d2).number, f"INV-{year}-00001")

    def test_custom_number_validation(self):
        club = make_club("Validate", country="BE")
        due = Due.objects.create(club=club, plan=self.plan, amount=Decimal("100.00"), period_start=START)
        for bad in ("   ", "", "X" * 33, "INV-2026-00005", "INV-1999-1"):
            with self.assertRaises(ValueError, msg=bad):
                invoices_svc.issue_invoice(due, number=bad)
        self.assertFalse(Invoice.objects.exists())
        invoices_svc.issue_invoice(due, number="A-1")
        due2 = Due.objects.create(club=club, plan=self.plan, amount=Decimal("100.00"), period_start=START + datetime.timedelta(days=400))
        with self.assertRaises(BillingError):
            invoices_svc.issue_invoice(due2, number="A-1")

    def test_counter_ignores_existing_oddly_named_invoices(self):
        year = datetime.date.today().year
        club = make_club("Odd", country="BE")
        due = Due.objects.create(club=club, plan=self.plan, amount=Decimal("100.00"), period_start=START)
        Invoice.objects.create(due=due, number=f"INV-{year}-ABC", issued_at=START)
        d2 = Due.objects.create(club=club, plan=self.plan, amount=Decimal("100.00"), period_start=START + datetime.timedelta(days=400))
        self.assertEqual(invoices_svc.issue_invoice(d2).number, f"INV-{year}-00001")

    def test_numbers_are_immutable(self):
        self.assertFalse(hasattr(invoices_svc, "change_number"))
        self.assertFalse(Invoice._meta.get_field("number").editable)


class RenewalTests(BillingTestBase):
    def test_upcoming_renewals(self):
        club = make_club("Renew", country="BE")
        from features.models import Subscription

        Subscription.objects.create(club=club, plan=self.plan, status=Subscription.ACTIVE) if hasattr(Subscription, "ACTIVE") else Subscription.objects.create(club=club, plan=self.plan)
        self.open(club)
        rows = dues_svc.upcoming_renewals(datetime.date(2026, 6, 1))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["next_period_start"], datetime.date(2027, 1, 1))
        self.assertFalse(rows[0]["opened"])


D = datetime.date


class GraceRuleTests(BillingTestBase):
    def test_late_opened_period_gets_full_grace_after_opening(self):
        club = make_club("Late", country="BE")
        with at(D(2026, 3, 1)):
            due = self.open(club, start=START)  # backdated: started 2026-01-01, opened 2026-03-01
        self.assertEqual(due.grace_until, D(2026, 3, 31))

    def test_advance_period_counts_grace_from_its_start(self):
        club = make_club("Early", country="BE")
        with at(D(2026, 3, 1)):
            due = self.open(club, start=D(2026, 6, 1))
        self.assertEqual(due.grace_until, D(2026, 7, 1))


class TrialTests(BillingTestBase):
    def trial(self, name="Tri", days=14):
        club = make_club(name, country="BE", billed=False)
        with at(START):
            billing, due = dues_svc.subscribe(club, self.plan, start=START, trial_days=days)
        return club, billing, due

    def test_trial_period(self):
        club, billing, due = self.trial()
        self.assertTrue(due.is_trial)
        self.assertEqual((due.amount, due.vat_amount, due.status), (Decimal("0"), Decimal("0"), Due.PAID))
        self.assertEqual((due.period_start, due.period_end), (START, START + datetime.timedelta(days=13)))
        self.assertEqual(billing.trial_ends_at, due.period_end)
        self.assertTrue(Invoice.objects.filter(due=due).exists())
        sub = Subscription.objects.get(club=club, ends_at__isnull=True)
        self.assertEqual((sub.plan, sub.status), (self.plan, Subscription.TRIAL))
        ent = resolve_entitlements(club)
        self.assertTrue(ent.has("sponsoring"))
        self.assertEqual(ent.entitlement("sponsoring").source, "plan")

    def test_trial_only_for_unbilled_clubs(self):
        club = make_club("Billed", country="BE")
        with self.assertRaises(BillingError):
            dues_svc.subscribe(club, self.plan, trial_days=14)
        with self.assertRaises(BillingError):
            dues_svc.subscribe(make_club("Zero", billed=False), self.plan, trial_days=0)

    def test_paid_period_follows_in_advance_and_converts(self):
        club, billing, trial = self.trial()
        with at(START + datetime.timedelta(days=2)):
            self.assertEqual(dues_svc.subscriptions_due_for_renewal(), [billing])  # lead days reached right away
            paid = dues_svc.renew(billing)
        self.assertEqual(paid.period_start, trial.period_end + datetime.timedelta(days=1))
        self.assertEqual((paid.amount, paid.status, paid.is_trial, paid.plan), (Decimal("100.00"), Due.UNPAID, False, self.plan))
        self.assertTrue(paid.is_issued_ahead(START + datetime.timedelta(days=2)))
        self.assertEqual(paid.grace_until, paid.period_start + datetime.timedelta(days=30))
        sub = Subscription.objects.get(club=club, ends_at__isnull=True)
        self.assertEqual((sub.plan, sub.status, sub.trial_ends_at), (self.plan, Subscription.ACTIVE, None))
        billing.refresh_from_db()
        self.assertIsNone(billing.trial_ends_at)

    def test_entitlements_never_drop_around_trial_end(self):
        club, billing, _trial = self.trial()
        # The subscription has no trial end date, so a moment long after the trial is still the same plan, even before
        # the paid period is opened (no dependency on the renewal job being on time).
        day_after = timezone.now() + datetime.timedelta(days=400)
        self.assertTrue(resolve_entitlements(club, now=day_after).has("sponsoring"))
        with at(START + datetime.timedelta(days=2)):
            dues_svc.renew(billing)
        ent = resolve_entitlements(club, now=day_after)
        self.assertEqual(ent.entitlement("sponsoring").source, "plan")

    def test_trial_not_converted_by_trial_period_itself(self):
        club, billing, _ = self.trial()
        billing.refresh_from_db()
        self.assertEqual(Subscription.objects.get(club=club, ends_at__isnull=True).status, Subscription.TRIAL)
        self.assertIsNotNone(billing.trial_ends_at)

    def test_plan_has_no_trial_flag(self):
        self.assertFalse(hasattr(self.plan.cycles.first(), "is_trial"))
        self.assertFalse(hasattr(ClubBilling(), "post_trial_plan"))


class LapsedTests(BillingTestBase):
    def billed_club(self, name, auto_renew):
        club = make_club(name, country="BE")
        ClubBilling.objects.filter(club=club).update(auto_renew=auto_renew)
        feature_services.subscribe(club, self.plan)
        self.open(club, start=START)  # runs to 2026-12-31
        return club

    def test_lapsed_without_auto_renew_is_picked_up(self):
        club = self.billed_club("Lapsed", auto_renew=False)
        self.assertEqual([b.club for b in dues_svc.subscriptions_due_for_renewal(D(2027, 1, 2))], [club])
        row = dues_svc.upcoming_renewals(D(2027, 1, 2))[0]
        self.assertTrue(row["lapsed"])

    def test_not_lapsed_without_auto_renew_is_left_alone(self):
        self.billed_club("Fine", auto_renew=False)
        for day in (D(2026, 6, 1), D(2026, 12, 20), D(2026, 12, 31)):
            self.assertEqual(dues_svc.subscriptions_due_for_renewal(day), [])
            self.assertEqual(dues_svc.upcoming_renewals(day), [])

    def test_auto_renew_follows_lead_days_and_flags_lapsed(self):
        self.billed_club("Auto", auto_renew=True)
        self.assertEqual(len(dues_svc.subscriptions_due_for_renewal(D(2026, 12, 1))), 1)
        self.assertEqual(dues_svc.subscriptions_due_for_renewal(D(2026, 11, 1)), [])
        self.assertFalse(dues_svc.upcoming_renewals(D(2026, 6, 1))[0]["lapsed"])
        self.assertTrue(dues_svc.upcoming_renewals(D(2027, 2, 1))[0]["lapsed"])

    def test_lapsed_period_opens_with_fresh_grace(self):
        club = self.billed_club("Catchup", auto_renew=False)
        billing = ClubBilling.objects.get(club=club)
        with at(D(2027, 1, 10)):
            due = dues_svc.renew(billing)
        self.assertEqual((due.period_start, due.grace_until), (D(2027, 1, 1), D(2027, 2, 9)))


class DowngradeTests(BillingTestBase):
    def overdue_club(self, name="Over", auto_archive=True):
        club = make_club(name, country="BE")
        ClubBilling.objects.filter(club=club).update(auto_archive=auto_archive)
        feature_services.subscribe(club, self.plan)
        with at(D(2026, 3, 1)):
            due = self.open(club, start=START)  # grace until 2026-03-31
        return club, due

    def test_downgrade_and_entitlement_fallback(self):
        from .tasks import downgrade_overdue_clubs

        club, _ = self.overdue_club()
        with at(D(2026, 3, 20)):  # still in grace
            downgrade_overdue_clubs()
        self.assertIsNone(ClubBilling.objects.get(club=club).downgraded_at)
        self.assertTrue(resolve_entitlements(club).has("sponsoring"))
        with at(D(2026, 4, 5)):
            downgrade_overdue_clubs(dry_run=True)
            self.assertIsNone(ClubBilling.objects.get(club=club).downgraded_at)
            downgrade_overdue_clubs()
            downgrade_overdue_clubs()  # idempotent
        self.assertIsNotNone(ClubBilling.objects.get(club=club).downgraded_at)
        from controlpanel.models import PlatformAuditLog

        self.assertEqual(PlatformAuditLog.objects.filter(action="billing.club_downgraded", club=club).count(), 1)
        ent = resolve_entitlements(club)
        self.assertFalse(ent.has("sponsoring"))
        self.assertEqual((ent.limit("max_members"), ent.entitlement("max_members").source), (50, "overdue"))
        self.assertIsNone(club.archived_at)
        self.assertTrue(resolve_entitlements(club, bypass=True).has("sponsoring"))

    def test_entitlement_query_count_is_not_higher_when_downgraded(self):
        club, _ = self.overdue_club()
        with CaptureQueriesContext(connection) as normal:
            resolve_entitlements(club)
        ClubBilling.objects.filter(club=club).update(downgraded_at=datetime.datetime.now(datetime.UTC))
        with CaptureQueriesContext(connection) as downgraded:
            resolve_entitlements(club)
        self.assertEqual(len(downgraded), len(normal))

    def test_downgrades_even_without_auto_archive(self):
        from .tasks import downgrade_overdue_clubs

        club, _ = self.overdue_club("NoArchive", auto_archive=False)
        with at(D(2026, 4, 5)):
            downgrade_overdue_clubs()
        self.assertIsNotNone(ClubBilling.objects.get(club=club).downgraded_at)

    def test_payment_clears_downgrade_immediately(self):
        from controlpanel.models import PlatformAuditLog

        from .tasks import downgrade_overdue_clubs

        club, due = self.overdue_club()
        with at(D(2026, 4, 5)):
            downgrade_overdue_clubs()
            dues_svc.record_payment(due, amount=Decimal("121.00"))
        self.assertIsNone(ClubBilling.objects.get(club=club).downgraded_at)
        self.assertTrue(resolve_entitlements(club).has("sponsoring"))
        self.assertTrue(PlatformAuditLog.objects.filter(action="billing.club_restored_after_payment", club=club).exists())

    def test_waive_clears_downgrade_and_task_clears_stale_ones(self):
        from .tasks import downgrade_overdue_clubs

        club, due = self.overdue_club()
        with at(D(2026, 4, 5)):
            downgrade_overdue_clubs()
            dues_svc.waive(due)
        self.assertIsNone(ClubBilling.objects.get(club=club).downgraded_at)
        ClubBilling.objects.filter(club=club).update(downgraded_at=datetime.datetime.now(datetime.UTC))
        with at(D(2026, 4, 5)):
            downgrade_overdue_clubs(dry_run=True)
            self.assertIsNotNone(ClubBilling.objects.get(club=club).downgraded_at)
            downgrade_overdue_clubs()
        self.assertIsNone(ClubBilling.objects.get(club=club).downgraded_at)

    def test_archive_only_after_archive_after_days(self):
        from .tasks import archive_overdue_clubs, downgrade_overdue_clubs

        club, _ = self.overdue_club()
        with at(D(2026, 4, 5)):
            self.assertEqual(list(dues_svc.archivable_clubs()), [])  # not downgraded yet
            downgrade_overdue_clubs()
            self.assertEqual(list(dues_svc.archivable_clubs()), [])  # just downgraded, 30 days to go
            archive_overdue_clubs()
        club.refresh_from_db()
        self.assertIsNone(club.archived_at)
        long_ago = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=31)
        ClubBilling.objects.filter(club=club).update(downgraded_at=long_ago)
        with at(D(2026, 4, 5)):
            archive_overdue_clubs(dry_run=True)
            club.refresh_from_db()
            self.assertIsNone(club.archived_at)
            archive_overdue_clubs()
        club.refresh_from_db()
        self.assertIsNotNone(club.archived_at)

    def test_archive_after_zero_days_and_auto_archive_off(self):
        club, _ = self.overdue_club("Zero")
        off, _ = self.overdue_club("Off", auto_archive=False)
        s = BillingSettings.current()
        s.archive_after_days = 0
        s.save()
        now = datetime.datetime.now(datetime.UTC)
        ClubBilling.objects.filter(club__in=[club, off]).update(downgraded_at=now - datetime.timedelta(seconds=1))
        with at(D(2026, 4, 5)):
            self.assertEqual(list(dues_svc.archivable_clubs()), [club])

    def test_reminder_text_mentions_downgrade_and_archive_days(self):
        from django.core import mail

        from .services import reminders

        _club, due = self.overdue_club("Remind")
        s = BillingSettings.current()
        s.archive_after_days = 14
        s.save()
        with mock.patch.object(reminders, "club_recipients", return_value=["a@example.com"]):
            self.assertTrue(reminders.send_reminder(due, reminders.ERROR))
        self.assertIn("moved to the free plan and archived 14 days later", mail.outbox[-1].body)


# --- plan change: prorate the running period and start a new cycle ----------------------------------------------------

from ninja.testing import TestClient  # noqa: E402

from controlpanel.api import urls as _api_urls  # noqa: E402,F401  (attaches the routers)
from controlpanel.api.auth import PlatformAuth  # noqa: E402
from controlpanel.api.core import api  # noqa: E402

from .models import ClubCreditEntry, CreditNote, CreditNoteSequence, DuePayment  # noqa: E402
from .services import credit as credit_svc  # noqa: E402
from .services import credit_notes as credit_notes_svc  # noqa: E402
from .services import plan_change as pc  # noqa: E402

SWITCH = D(2026, 7, 2)  # 182 of the 365 days of 2026-01-01..2026-12-31 are used; prorated net 100 * 182/365 = 49.86
M = Decimal


def counts():
    return {
        "due": Due.objects.count(),
        "invoice": Invoice.objects.count(),
        "billing": ClubBilling.objects.count(),
        "subscription": Subscription.objects.count(),
        "invoice_seq": list(InvoiceSequence.objects.values_list("scope", "last_number")),
        "credit_note": CreditNote.objects.count(),
        "credit_seq": list(CreditNoteSequence.objects.values_list("scope", "last_number")),
        "ledger": ClubCreditEntry.objects.count(),
        "payment": DuePayment.objects.count(),
    }


class PlanChangeBase(BillingTestBase):
    def setUp(self):
        super().setUp()
        self.big = Plan.objects.create(code="big-test", name="Big")
        PlanPrice.objects.create(plan=self.big, active_from=D(2000, 1, 1), amount=M("300.00"))
        self.small = Plan.objects.create(code="small-test", name="Small")
        PlanPrice.objects.create(plan=self.small, active_from=D(2000, 1, 1), amount=M("40.00"))
        self.staff = get_user_model().objects.create(email="staff@example.com", is_active=True)

    def billed(self, name="Alpha", plan=None, country="BE", vat_id="", paid=None, start=START):
        club = make_club(name, country=country, vat_id=vat_id)
        feature_services.subscribe(club, plan or self.plan)
        with at(start):
            due = self.open(club, plan=plan, start=start)
        if paid:
            dues_svc.record_payment(due, amount=paid)
            due.refresh_from_db()
        return club, due

    def change(self, club, plan, day=SWITCH, effective=None):
        with at(day):
            return pc.execute_plan_change(club, plan, effective_date=effective, actor=self.staff)

    def preview(self, club, plan, day=SWITCH, effective=None):
        with at(day):
            return pc.build_plan_change(club, plan, effective)

    def assert_conserved(self, club):
        """No money is created or lost: cash received = what periods keep + credit held; ledger = balance."""
        payments = list(DuePayment.objects.filter(due__club=club))
        cash = sum((p.amount for p in payments if p.method != DuePayment.CREDIT), M("0"))
        credit_paid = sum((p.amount for p in payments if p.method == DuePayment.CREDIT), M("0"))
        dues = list(Due.objects.filter(club=club))
        kept = sum((d.amount_paid for d in dues), M("0"))
        transferred = sum((d.credit_transferred for d in dues), M("0"))
        balance = credit_svc.credit_balance(club)
        self.assertEqual(kept, cash + credit_paid - transferred)  # amount_paid = payments - credit_transferred
        self.assertEqual(balance, transferred - credit_paid)  # ledger: + moved to credit, - credit used
        self.assertEqual(cash, kept + balance)
        self.assertTrue(balance >= 0)
        for d in dues:
            self.assertTrue(d.amount_paid >= 0)


class ProrationMathTests(PlanChangeBase):
    def test_prorated_figures_and_dates(self):
        club, due = self.billed()
        result = self.change(club, self.big)
        due.refresh_from_db()
        self.assertEqual((due.period_start, due.period_end, due.original_period_end), (START, D(2026, 7, 1), D(2026, 12, 31)))
        self.assertEqual((due.amount, due.vat_amount, due.total), (M("49.86"), M("10.47"), M("60.33")))
        self.assertEqual((due.original_amount, due.original_vat_amount, due.original_total), (M("100.00"), M("21.00"), M("121.00")))
        self.assertTrue(due.is_prorated)
        self.assertIsNotNone(due.prorated_at)
        old = result.change.old
        self.assertEqual((old.total_days, old.used_days, old.unused_days), (365, 182, 183))

    def test_rounding_is_half_up_to_cents_and_vat_uses_frozen_rate(self):
        d = Due(amount=M("0.05"), vat_rate=M("21.00"), vat_amount=M("0.01"))
        m = pc.prorate(d, 1, 2)  # 0.025 -> 0.03; 0.03 * 21% = 0.0063 -> 0.01
        self.assertEqual((m.net, m.vat), (M("0.03"), M("0.01")))
        d = Due(amount=M("10.00"), vat_rate=M("6.00"), vat_amount=M("0.60"))
        self.assertEqual(pc.prorate(d, 1, 3), pc.Money(M("3.33"), M("0.20")))
        d = Due(amount=M("10.00"), vat_rate=M("21.00"), vat_amount=M("2.10"))
        self.assertEqual(pc.prorate(d, 2, 3).net, M("6.67"))

    def test_vat_at_the_periods_own_rate_even_after_the_rate_changed(self):
        club, due = self.billed()
        s = BillingSettings.current()
        s.vat_rate = M("6.00")
        s.save()
        cache.clear()
        self.change(club, self.big)
        due.refresh_from_db()
        self.assertEqual(due.vat_rate, M("21.00"))
        self.assertEqual(due.vat_amount, M("10.47"))
        new = Due.objects.get(club=club, plan=self.big)
        self.assertEqual((new.vat_rate, new.vat_amount), (M("6.00"), M("18.00")))

    def test_reverse_charge_stays_reverse_charge(self):
        club, due = self.billed("Dutch", country="NL", vat_id="NL123B01", paid=M("100.00"))
        result = self.change(club, self.big)
        due.refresh_from_db()
        self.assertEqual((due.amount, due.vat_amount, due.reverse_charge), (M("49.86"), M("0"), True))
        note = result.credit_notes[0]
        self.assertEqual((note.net, note.vat, note.total, note.reverse_charge), (M("50.14"), M("0"), M("50.14"), True))
        self.assertTrue(result.new_due.reverse_charge)
        self.assertIn("VAT reverse charged", credit_notes_svc.credit_note_html(note))
        self.assert_conserved(club)


class MoneyScenarioTests(PlanChangeBase):
    def test_fully_paid_old_period_overpayment_becomes_credit_and_pays_new_invoice(self):
        club, due = self.billed(paid=M("121.00"))
        result = self.change(club, self.big)
        due.refresh_from_db()
        self.assertEqual((due.status, due.amount_paid, due.balance, due.credit_transferred), (Due.PAID, M("60.33"), M("0.00"), M("60.67")))
        self.assertEqual(sum(p.amount for p in due.payments.all()), M("121.00"))  # payment history untouched
        entries = list(ClubCreditEntry.objects.filter(club=club).order_by("created"))
        self.assertEqual([(e.kind, e.amount) for e in entries], [("overpayment", M("60.67")), ("applied", M("-60.67"))])
        new = result.new_due
        self.assertEqual((new.amount, new.vat_amount, new.total, new.amount_paid, new.status), (M("300.00"), M("63.00"), M("363.00"), M("60.67"), Due.PARTIAL))
        pay = new.payments.get()
        self.assertEqual((pay.method, pay.amount, pay.reference), ("credit", M("60.67"), result.credit_notes[0].number))
        self.assertEqual(credit_svc.credit_balance(club), M("0"))
        self.assertEqual(result.credit_notes[0].total, M("60.67"))
        self.assert_conserved(club)

    def test_unpaid_old_period_is_just_reduced(self):
        club, due = self.billed()
        result = self.change(club, self.big)
        due.refresh_from_db()
        self.assertEqual((due.status, due.total, due.amount_paid, due.balance), (Due.UNPAID, M("60.33"), M("0"), M("60.33")))
        self.assertFalse(ClubCreditEntry.objects.exists())
        self.assertEqual(len(result.credit_notes), 1)  # the credit note still documents the reduction
        self.assertEqual(result.credit_notes[0].total, M("60.67"))
        self.assertEqual((result.new_due.amount_paid, result.new_due.status), (M("0"), Due.UNPAID))
        self.assertEqual(result.change.amount_payable, M("363.00"))
        self.assert_conserved(club)

    def test_part_paid_below_prorated_total_lowers_open_balance_only(self):
        club, due = self.billed(paid=M("50.00"))
        self.change(club, self.big)
        due.refresh_from_db()
        self.assertEqual((due.status, due.amount_paid, due.balance, due.credit_transferred), (Due.PARTIAL, M("50.00"), M("10.33"), M("0")))
        self.assertFalse(ClubCreditEntry.objects.exclude(kind="applied").exists())
        self.assert_conserved(club)

    def test_part_paid_above_prorated_total_only_the_surplus_is_credit(self):
        club, due = self.billed(paid=M("100.00"))
        self.change(club, self.big)
        due.refresh_from_db()
        self.assertEqual((due.status, due.amount_paid, due.credit_transferred), (Due.PAID, M("60.33"), M("39.67")))
        self.assertEqual(ClubCreditEntry.objects.get(kind="overpayment").amount, M("39.67"))
        self.assert_conserved(club)

    def test_downgrade_credit_exceeding_new_total_is_carried_forward(self):
        club, due = self.billed(plan=self.big, paid=M("363.00"))
        result = self.change(club, self.small)
        due.refresh_from_db()
        self.assertEqual((due.total, due.credit_transferred), (M("181.00"), M("182.00")))
        new = result.new_due
        self.assertEqual((new.total, new.status, new.amount_paid), (M("48.40"), Due.PAID, M("48.40")))
        self.assertEqual(credit_svc.credit_balance(club), M("133.60"))
        self.assertEqual((result.change.credit_applied, result.change.amount_payable, result.change.credit_balance_after), (M("48.40"), M("0.00"), M("133.60")))
        # the next period of this club (opened later) is paid from the credit that is left
        with at(D(2027, 6, 1)):
            nxt = dues_svc.renew(ClubBilling.objects.get(club=club))
        self.assertEqual((nxt.period_start, nxt.status, nxt.amount_paid), (D(2027, 7, 2), Due.PAID, M("48.40")))
        self.assertEqual(nxt.payments.get().reference, "club credit")
        self.assertEqual(credit_svc.credit_balance(club), M("85.20"))
        self.assert_conserved(club)

    def test_credit_never_exceeds_what_the_invoice_needs_and_empty_credit_does_nothing(self):
        club, _ = self.billed(paid=M("121.00"))
        self.change(club, self.big)
        self.assertEqual(credit_svc.apply_credit(Due.objects.get(club=club, plan=self.big)), M("0"))  # nothing left
        self.assert_conserved(club)

    def test_credit_payments_cannot_be_recorded_or_removed_by_hand(self):
        club, due = self.billed(paid=M("121.00"))
        result = self.change(club, self.big)
        with self.assertRaises(BillingError):
            dues_svc.record_payment(result.new_due, amount=M("10.00"), method="credit")
        with self.assertRaises(BillingError):
            dues_svc.remove_payment(result.new_due.payments.get())
        with self.assertRaises(BillingError):
            dues_svc.remove_payment(due.payments.get())  # part of it was moved to the club's credit

    def test_paying_the_rest_of_a_prorated_period_works(self):
        club, due = self.billed(paid=M("50.00"))
        self.change(club, self.big)
        due.refresh_from_db()
        with self.assertRaises(BillingError):
            dues_svc.record_payment(due, amount=M("10.34"))
        dues_svc.record_payment(due, amount=M("10.33"))
        due.refresh_from_db()
        self.assertEqual(due.status, Due.PAID)


class NewCycleTests(PlanChangeBase):
    def test_new_period_dates_price_vat_grace_invoice_and_subscription(self):
        club, _ = self.billed()
        before = Invoice.objects.get().number
        result = self.change(club, self.big)
        new = result.new_due
        self.assertEqual((new.plan, new.period_start, new.period_end), (self.big, SWITCH, D(2027, 7, 1)))
        self.assertEqual((new.amount, new.vat_rate, new.vat_amount), (M("300.00"), M("21.00"), M("63.00")))
        self.assertEqual(new.grace_until, SWITCH + datetime.timedelta(days=30))
        self.assertEqual((before, new.invoice.number), ("INV-2026-00001", "INV-2026-00002"))
        subs = Subscription.objects.filter(club=club)
        self.assertEqual(subs.filter(ends_at__isnull=True).get().plan, self.big)
        self.assertEqual(subs.count(), 2)
        self.assertEqual(feature_services.EntitlementLog.objects.filter(club=club, reason="Plan change").count(), 1)

    def test_price_on_the_switch_date_applies(self):
        PlanPrice.objects.create(plan=self.big, active_from=D(2026, 6, 1), amount=M("360.00"))
        club, _ = self.billed()
        self.assertEqual(self.change(club, self.big).new_due.amount, M("360.00"))

    def test_entitlements_switch_with_the_plan(self):
        club, _ = self.billed()
        self.assertTrue(resolve_entitlements(club).has("sponsoring"))
        self.change(club, self.small)  # the small plan has no features
        self.assertFalse(resolve_entitlements(club).has("sponsoring"))

    def test_anniversary_moves_to_the_switch_date(self):
        club, _ = self.billed()
        self.change(club, self.big)
        with at(D(2027, 6, 1)):
            rows = dues_svc.upcoming_renewals(D(2027, 6, 1))
        self.assertEqual(rows[0]["next_period_start"], D(2027, 7, 2))

    def test_back_dated_switch_within_the_current_period(self):
        club, due = self.billed()
        result = self.change(club, self.big, day=D(2026, 7, 10), effective=D(2026, 7, 2))
        due.refresh_from_db()
        self.assertEqual(due.period_end, D(2026, 7, 1))
        self.assertEqual(result.new_due.period_start, D(2026, 7, 2))
        self.assertTrue(any("back-dated" in w for w in result.change.warnings))
        self.assertEqual(result.new_due.grace_until, D(2026, 7, 10) + datetime.timedelta(days=30))  # grace rule: opened later than it starts

    def test_trial_switch_shortens_the_trial_and_starts_paid_period(self):
        club = make_club("Triallist", country="BE", billed=False)
        with at(START):
            billing, trial = dues_svc.subscribe(club, self.plan, start=START, trial_days=14)
        result = self.change(club, self.big, day=START + datetime.timedelta(days=5))
        trial.refresh_from_db()
        self.assertEqual((trial.period_end, trial.original_period_end, trial.amount, trial.status), (START + datetime.timedelta(days=4), START + datetime.timedelta(days=13), M("0"), Due.PAID))
        self.assertEqual(result.credit_notes, [])
        billing.refresh_from_db()
        self.assertIsNone(billing.trial_ends_at)
        sub = Subscription.objects.get(club=club, ends_at__isnull=True)
        self.assertEqual((sub.plan, sub.status), (self.big, Subscription.ACTIVE))
        self.assertEqual(result.new_due.period_start, START + datetime.timedelta(days=5))
        self.assertFalse(result.new_due.is_trial)
        self.assertTrue(result.change.trial_replaced)
        self.assertFalse(CreditNote.objects.exists())

    def test_no_period_covers_the_date_continues_as_lapsed(self):
        club, due = self.billed(paid=M("121.00"))  # runs to 2026-12-31
        result = self.change(club, self.big, day=D(2027, 3, 10))
        due.refresh_from_db()
        self.assertEqual((due.period_end, due.is_prorated, due.total), (D(2026, 12, 31), False, M("121.00")))
        self.assertIsNone(result.change.old)
        self.assertEqual(result.new_due.period_start, D(2027, 1, 1))  # the lapsed time is billed on the new plan
        self.assertEqual(result.new_due.grace_until, D(2027, 3, 10) + datetime.timedelta(days=30))
        self.assertEqual(result.credit_notes, [])
        self.assertTrue(result.change.warnings)

    def test_billed_club_without_any_period_starts_today(self):
        club = make_club("Empty", country="BE")
        result = self.change(club, self.big, day=D(2026, 3, 1))
        self.assertEqual(result.new_due.period_start, D(2026, 3, 1))
        with self.assertRaises(pc.PlanChangeDateError):
            self.change(make_club("Empty2", country="BE"), self.big, day=D(2026, 3, 1), effective=D(2026, 2, 1))


class AdvanceInvoicedTests(PlanChangeBase):
    def advance_club(self, advance_paid=None):
        club, due = self.billed(paid=M("121.00"))
        with at(D(2026, 6, 1)):
            adv = self.open(club, start=D(2027, 1, 1))
        if advance_paid:
            dues_svc.record_payment(adv, amount=advance_paid)
        return club, due, adv

    def test_future_period_is_cancelled_with_a_full_credit_note(self):
        club, _due, adv = self.advance_club()
        result = self.change(club, self.big)
        adv.refresh_from_db()
        self.assertEqual(adv.status, Due.CANCELLED)
        self.assertTrue(Invoice.objects.filter(due=adv).exists())  # the invoice stays for the record
        notes = {n.due_id: n for n in result.credit_notes}
        self.assertEqual(notes[adv.pk].total, M("121.00"))
        self.assertEqual(notes[adv.pk].invoice, adv.invoice)
        self.assertEqual(notes[adv.pk].reason, "Plan change: period 2027-01-01 to 2027-12-31 on Pro cancelled")
        self.assertEqual(result.cancelled_dues, [adv])
        self.assertEqual(dues_svc.owing_dues().filter(pk=adv.pk).count(), 0)
        self.assertFalse(any(d.pk == adv.pk for d, _ in __import__("billing.services.reminders", fromlist=["x"]).reminders_to_send(SWITCH, force=True)))
        self.assert_conserved(club)

    def test_money_paid_on_the_cancelled_period_becomes_credit_and_pays_the_new_one(self):
        club, _due, adv = self.advance_club(advance_paid=M("121.00"))
        result = self.change(club, self.big)
        adv.refresh_from_db()
        self.assertEqual((adv.status, adv.amount_paid, adv.credit_transferred), (Due.CANCELLED, M("0"), M("121.00")))
        self.assertEqual(sum(p.amount for p in adv.payments.all()), M("121.00"))
        kinds = sorted((e.kind, e.amount) for e in ClubCreditEntry.objects.filter(club=club, amount__gt=0))
        self.assertEqual(kinds, [("cancelled_period", M("121.00")), ("overpayment", M("60.67"))])
        self.assertEqual(result.new_due.amount_paid, M("181.67"))  # 121.00 + 60.67 of the 363.00
        self.assertEqual(credit_svc.credit_balance(club), M("0"))
        with self.assertRaises(BillingError):
            dues_svc.remove_payment(adv.payments.get())  # cancelled periods stay cancelled
        self.assert_conserved(club)

    def test_cancelled_periods_are_left_out_of_totals_and_cycles(self):
        from controlpanel.stats import _dues_owed, _invoices_to_send

        club, _due, adv = self.advance_club()
        self.change(club, self.big)
        owed = Decimal(_dues_owed(None))
        self.assertEqual(owed, M("363.00") - M("60.67"))  # the new period less the credit from the paid old one; the cancelled one is not owed
        self.assertEqual(dues_svc.next_period_start(club), D(2027, 7, 2))
        self.assertEqual(_invoices_to_send(None), Invoice.objects.exclude(due=adv).filter(sent_at__isnull=True).exclude(due__amount=0).count())

    def test_prorated_amounts_flow_into_owed_totals(self):
        from controlpanel.stats import _dues_owed

        club, _due = self.billed()
        self.change(club, self.big)
        self.assertEqual(Decimal(_dues_owed(None)), M("60.33") + M("363.00"))


class CreditNoteTests(PlanChangeBase):
    def test_numbers_are_gapless_per_year_and_separate_from_invoices(self):
        a, _ = self.billed("A1", paid=M("121.00"))
        b, _ = self.billed("B1")
        n1 = self.change(a, self.big).credit_notes[0]
        n2 = self.change(b, self.big).credit_notes[0]
        self.assertEqual((n1.number, n2.number), ("CN-2026-00001", "CN-2026-00002"))
        self.assertEqual(CreditNoteSequence.objects.get(scope="2026").last_number, 2)
        self.assertEqual(InvoiceSequence.objects.get(scope="2026").last_number, 4)  # 2 original + 2 new, unaffected by credit notes
        with at(D(2027, 2, 3)):
            c, _ = self.billed("C1", start=D(2027, 1, 1))
        n3 = self.change(c, self.big, day=D(2027, 2, 3)).credit_notes[0]
        self.assertEqual(n3.number, "CN-2027-00001")

    def test_forced_rollback_does_not_burn_a_number(self):
        club, due = self.billed(paid=M("121.00"))
        before = counts()
        with mock.patch.object(CreditNote.objects, "create", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
            self.change(club, self.big)
        self.assertEqual(counts(), before)
        due.refresh_from_db()
        self.assertFalse(due.is_prorated)
        self.assertEqual(self.change(club, self.big).credit_notes[0].number, "CN-2026-00001")

    def test_rollback_after_the_credit_note_also_releases_the_number(self):
        club, _due = self.billed()
        before = counts()
        with mock.patch.object(pc.feature_services, "subscribe", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
            self.change(club, self.big)
        self.assertEqual(counts(), before)
        self.assertEqual(self.change(club, self.big).credit_notes[0].number, "CN-2026-00001")

    def test_snapshot_and_immutability_and_original_invoice_untouched(self):
        club, due = self.billed(vat_id="BE1", paid=M("121.00"))
        inv = due.invoice
        snapshot, sent, number = dict(inv.snapshot), inv.sent_at, inv.number
        note = self.change(club, self.big).credit_notes[0]
        inv.refresh_from_db()
        self.assertEqual((inv.snapshot, inv.sent_at, inv.number), (snapshot, sent, number))
        self.assertEqual(inv.snapshot["total"], "121.00")
        self.assertEqual(note.invoice, inv)
        self.assertEqual(note.reason, "Plan change: unused time 2026-07-02 to 2026-12-31 on Pro")
        self.assertEqual((note.net, note.vat, note.total, note.vat_rate, note.reverse_charge), (M("50.14"), M("10.53"), M("60.67"), M("21.00"), False))
        snap = note.snapshot
        self.assertEqual((snap["issuer"]["legal_name"], snap["invoice_number"], snap["total"]), ("Issuer BV", number, "60.67"))
        s = BillingSettings.current()
        s.legal_name = "Changed BV"
        s.save()
        html = credit_notes_svc.credit_note_html(CreditNote.objects.get(pk=note.pk))
        self.assertIn("Issuer BV", html)
        self.assertNotIn("Changed BV", html)
        self.assertIn(note.number, html)
        self.assertIn(number, html)
        note.reason = "edited"
        with self.assertRaises(ValueError):
            note.save()
        self.assertFalse(CreditNote._meta.get_field("number").editable)

    def test_no_credit_note_for_a_zero_invoice(self):
        free = Plan.objects.create(code="free-x", name="Free X")
        PlanPrice.objects.create(plan=free, active_from=D(2000, 1, 1), amount=0)
        club, due = self.billed(plan=free)
        result = self.change(club, self.big)
        self.assertEqual(result.credit_notes, [])
        due.refresh_from_db()
        self.assertEqual((due.status, due.total, due.period_end), (Due.PAID, M("0"), D(2026, 7, 1)))

    def test_paid_invoice_html_shows_credit_as_credit_applied(self):
        club, _due = self.billed(paid=M("121.00"))
        result = self.change(club, self.big)
        html = invoices_svc.invoice_html(result.new_due.invoice)
        self.assertIn("Credit applied", html)

    def test_pdf_renders_or_reports_unavailable(self):
        club, _ = self.billed()
        note = self.change(club, self.big).credit_notes[0]
        try:
            import weasyprint  # noqa: F401
        except ImportError, OSError:
            with self.assertRaises(BillingError):
                credit_notes_svc.credit_note_pdf(note)
        else:
            self.assertTrue(credit_notes_svc.credit_note_pdf(note).startswith(b"%PDF"))


class ValidationTests(PlanChangeBase):
    def assert_nothing_changed(self, fn, exc=BillingError):
        before = counts()
        with self.assertRaises(exc):
            fn()
        self.assertEqual(counts(), before)

    def test_same_plan_inactive_plan_and_missing_price(self):
        club, _ = self.billed(paid=M("121.00"))
        self.assert_nothing_changed(lambda: self.change(club, self.plan))
        self.big.is_active = False
        self.big.save()
        self.assert_nothing_changed(lambda: self.change(club, self.big))
        unpriced = Plan.objects.create(code="unpriced", name="Unpriced")
        self.assert_nothing_changed(lambda: self.change(club, unpriced))
        late = Plan.objects.create(code="late", name="Late")
        PlanPrice.objects.create(plan=late, active_from=D(2026, 9, 1), amount=M("10.00"))
        self.assert_nothing_changed(lambda: self.change(club, late))  # no price on the switch date yet
        self.assertEqual(self.change(club, late, day=D(2026, 9, 5)).new_due.amount, M("10.00"))

    def test_date_range(self):
        club, _due = self.billed()
        for bad in (D(2026, 7, 3), D(2026, 1, 1), D(2025, 12, 1)):  # future, the period's start day, before it
            self.assert_nothing_changed(lambda bad=bad: self.change(club, self.big, effective=bad), pc.PlanChangeDateError)
        self.assertIsInstance(pc.PlanChangeDateError("x"), BillingError)
        self.assertEqual(self.preview(club, self.big, effective=D(2026, 1, 2)).old.used_days, 1)

    def test_period_started_today_cannot_be_switched_yet(self):
        club, _ = self.billed()
        self.assert_nothing_changed(lambda: self.change(club, self.big, day=START), pc.PlanChangeDateError)

    def test_unbilled_and_archived_clubs(self):
        unbilled = make_club("Unbilled", billed=False)
        self.assert_nothing_changed(lambda: self.change(unbilled, self.big))
        club, _ = self.billed()
        Club.objects.filter(pk=club.pk).update(archived_at=timezone.now())
        club.refresh_from_db()
        self.assert_nothing_changed(lambda: self.change(club, self.big))

    def test_a_prorated_period_cannot_be_prorated_twice(self):
        club, due = self.billed()
        self.change(club, self.big)
        with self.assertRaises(BillingError):
            pc._plan_old_period(Due.objects.get(pk=due.pk), D(2026, 6, 1))

    def test_collision_with_an_existing_period_start(self):
        club, _due = self.billed()
        with at(D(2026, 6, 1)):
            dues_svc.open_period(club, start=D(2027, 1, 1), plan=self.plan)
        self.change(club, self.big, day=D(2026, 7, 2))  # cancels 2027-01-01; later, a lapsed new start of 2027-07-02 is free
        self.assertEqual(Due.objects.filter(club=club, status=Due.CANCELLED).count(), 1)


class ApiBase(PlanChangeBase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(PlatformAuth, "__call__", self._auth)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(api)

    def _auth(self, request):
        request.user = self.staff
        return self.staff

    def post(self, path, body=None, day=SWITCH, headers=None):
        with at(day):
            return self.client.post(path, json=body or {}, headers=headers or {})

    def get(self, path, day=SWITCH):
        with at(day):
            return self.client.get(path)


class ApiTests(ApiBase):
    def test_preview_writes_nothing_and_equals_the_execution(self):
        club, due = self.billed(paid=M("121.00"))
        base = f"/clubs/{club.pk}/billing/change-plan"
        before = counts()
        pre = self.post(base + "/preview", {"plan_id": str(self.big.pk)})
        self.assertEqual(pre.status_code, 200, pre.content)
        self.assertEqual(counts(), before)
        done = self.post(base, {"plan_id": str(self.big.pk)})
        self.assertEqual(done.status_code, 201, done.content)
        preview, executed = pre.json(), done.json()
        for key in preview:
            self.assertEqual(executed[key], preview[key], key)
        op = preview["old_period"]
        self.assertEqual((op["total_days"], op["used_days"], op["new_period_end"], op["overpayment_to_credit"]), (365, 182, "2026-07-01", "60.67"))
        self.assertEqual((op["original"], op["prorated"]), ({"net": "100.00", "vat": "21.00", "total": "121.00"}, {"net": "49.86", "vat": "10.47", "total": "60.33"}))
        self.assertEqual(preview["new_period"]["total"], "363.00")
        self.assertEqual((preview["credit_applied"], preview["amount_payable_after_credit"], preview["credit_balance_after"]), ("60.67", "302.33", "0.00"))
        self.assertEqual(preview["credit_notes_to_issue"], [{"for_invoice": "INV-2026-00001", "net": "50.14", "vat": "10.53", "total": "60.67"}])
        created = executed["created"]
        self.assertEqual(created["old_due_id"], str(due.pk))
        self.assertEqual(len(created["credit_note_ids"]), 1)
        self.assertEqual(executed["billing"]["credit_balance"], "0.00")
        self.assertEqual(executed["billing"]["plan"]["code"], "big-test")
        self.assertEqual(executed["credit_notes"][0]["number"], "CN-2026-00001")
        self.assertEqual(executed["credit_notes"][0]["applied_to"][0]["amount"], "60.67")

    def test_every_scenario_preview_matches_execution(self):
        scenarios = [
            lambda: self.billed("S1"),
            lambda: self.billed("S2", paid=M("50.00")),
            lambda: self.billed("S3", plan=self.big, paid=M("363.00")),
        ]
        for _i, make in enumerate(scenarios):
            club, _ = make()
            target = self.small if club.name == "S3" else self.big
            body = {"plan_id": str(target.pk)}
            base = f"/clubs/{club.pk}/billing/change-plan"
            pre = self.post(base + "/preview", body).json()
            done = self.post(base, body).json()
            for key in pre:
                self.assertEqual(done[key], pre[key], f"{club.name}: {key}")
            self.assert_conserved(club)

    def test_errors(self):
        club, _due = self.billed()
        base = f"/clubs/{club.pk}/billing/change-plan"
        r = self.post(base + "/preview", {"plan_id": str(self.big.pk), "effective_date": "2026-07-03"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "validation_error")
        self.assertIn("effective_date", r.json()["error"]["fields"])
        r = self.post(base, {"plan_id": str(self.plan.pk)})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "billing_error"))
        r = self.post(f"/clubs/{make_club('Nobill', billed=False).pk}/billing/change-plan/preview", {"plan_id": str(self.big.pk)})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "billing_error"))

    def test_idempotency_key_replays(self):
        club, _ = self.billed()
        base = f"/clubs/{club.pk}/billing/change-plan"
        h = {"Idempotency-Key": "abc"}
        first = self.post(base, {"plan_id": str(self.big.pk)}, headers=h)
        second = self.post(base, {"plan_id": str(self.big.pk)}, headers=h)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json()["created"], second.json()["created"])
        self.assertEqual(CreditNote.objects.count(), 1)
        self.assertEqual(Due.objects.filter(club=club).count(), 2)

    def test_back_doors_return_409_for_billed_clubs(self):
        club, _ = self.billed()
        msg = "This club is billed: use the billing plan change so the current period is prorated and a new cycle starts."
        before = counts()
        r = self.post(f"/clubs/{club.pk}/subscriptions", {"plan_id": str(self.big.pk)})
        self.assertEqual((r.status_code, r.json()["error"]["code"], r.json()["error"]["message"]), (409, "billing_error", msg))
        r = self.post(f"/clubs/{club.pk}/billing/subscribe", {"plan_id": str(self.big.pk)})
        self.assertEqual((r.status_code, r.json()["error"]["code"], r.json()["error"]["message"]), (409, "billing_error", msg))
        self.assertEqual(counts(), before)

    def test_unbilled_clubs_keep_the_old_behaviour(self):
        club = make_club("Free", billed=False)
        r = self.post(f"/clubs/{club.pk}/subscriptions", {"plan_id": str(self.big.pk)})
        self.assertEqual(r.status_code, 201, r.content)
        club2 = make_club("First", country="BE", billed=False)
        r = self.post(f"/clubs/{club2.pk}/billing/subscribe", {"plan_id": str(self.big.pk), "start": "2026-01-01"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Due.objects.filter(club=club2).count(), 1)

    def test_credit_notes_ledger_and_due_fields(self):
        club, due = self.billed(paid=M("121.00"))
        other, _ = self.billed("Other")
        self.post(f"/clubs/{club.pk}/billing/change-plan", {"plan_id": str(self.big.pk)})
        self.post(f"/clubs/{other.pk}/billing/change-plan", {"plan_id": str(self.big.pk)})
        listing = self.get("/billing/credit-notes").json()
        self.assertEqual(listing["count"], 2)
        self.assertEqual({n["number"] for n in listing["items"]}, {"CN-2026-00001", "CN-2026-00002"})
        self.assertEqual(self.get("/billing/credit-notes?search=Other").json()["count"], 1)
        self.assertEqual(self.get(f"/billing/credit-notes?club_id={club.pk}").json()["items"][0]["invoice_number"], "INV-2026-00001")
        note = self.get(f"/billing/credit-notes?club_id={club.pk}").json()["items"][0]
        one = self.get(f"/billing/credit-notes/{note['id']}").json()
        self.assertEqual(set(one), {"id", "number", "issued_at", "club", "invoice_number", "due_id", "net", "vat", "total", "vat_rate", "reverse_charge", "reason", "applied_to", "kind", "replacement_invoice_number"})
        self.assertEqual(one["due_id"], str(due.pk))
        pdf = self.get(f"/billing/credit-notes/{note['id']}/pdf")
        try:
            import weasyprint  # noqa: F401
        except ImportError, OSError:
            self.assertEqual((pdf.status_code, pdf.json()["error"]["code"]), (409, "billing_error"))
        else:
            self.assertEqual((pdf.status_code, pdf.content[:4]), (200, b"%PDF"))
        ledger = self.get(f"/clubs/{club.pk}/billing/credit").json()
        self.assertEqual(ledger["balance"], "0.00")
        self.assertEqual([(e["kind"], e["amount"], e["credit_note_number"]) for e in ledger["entries"]], [("applied", "-60.67", "CN-2026-00001"), ("overpayment", "60.67", "CN-2026-00001")])
        b = self.get(f"/clubs/{club.pk}/billing").json()
        self.assertEqual(b["credit_balance"], "0.00")
        self.assertEqual(b["credit_notes"][0]["number"], "CN-2026-00001")
        old = next(d for d in b["dues"] if d["id"] == str(due.pk))
        self.assertEqual((old["prorated"], old["original_total"], old["original_period_end"], old["total"], old["status"]), (True, "121.00", "2026-12-31", "60.33", "paid"))
        self.assertEqual(old["credit_notes"], [{"number": "CN-2026-00001", "total": "60.67", "kind": "plan_change"}])
        self.assertEqual(old["credit_transferred"], "60.67")
        self.assertEqual({p["method"] for d in b["dues"] for p in self.get(f"/billing/dues/{d['id']}").json()["payments"]}, {"bank_transfer", "credit"})

    def test_manual_credit_payments_are_refused_by_the_api(self):
        _club, due = self.billed()
        r = self.post(f"/billing/dues/{due.pk}/payments", {"amount": "10.00", "method": "credit"})
        self.assertEqual(r.status_code, 422)


# --- void and reissue --------------------------------------------------------------------------------------------------

from controlpanel.models import PlatformAuditLog  # noqa: E402

from .services import reissue as reissue_svc  # noqa: E402


class VoidReissueTests(ApiBase):
    def void(self, due, reason="Wrong bill-to address", number=None, headers=None, day=SWITCH):
        body = {"reason": reason}
        if number is not None:
            body["number"] = number
        return self.post(f"/billing/dues/{due.pk}/invoice/void-and-reissue", body, day=day, headers=headers)

    def position(self, club):
        return {
            "dues": [(d.pk, d.amount, d.vat_amount, d.total, d.amount_paid, d.status, d.credit_transferred, d.grace_until, d.paid_at, d.period_end, d.original_amount) for d in Due.objects.filter(club=club).order_by("period_start")],
            "payments": sorted((p.pk, p.amount, p.method) for p in DuePayment.objects.filter(due__club=club)),
            "ledger": sorted((e.pk, e.amount, e.kind) for e in ClubCreditEntry.objects.filter(club=club)),
            "balance": credit_svc.credit_balance(club),
            "owed": sum((d.balance for d in Due.objects.filter(club=club) if d.is_owing), M("0")),
        }

    def test_void_with_vat_issues_a_full_credit_note_and_changes_no_money(self):
        club, due = self.billed(vat_id="BE1", paid=M("50.00"))
        before_pos = self.position(club)
        r = self.void(due)
        self.assertEqual(r.status_code, 201, r.content)
        data = r.json()
        self.assertEqual(self.position(club), before_pos)
        self.assert_conserved(club)
        old, new, note = data["voided"], data["invoice"], data["credit_note"]
        self.assertEqual((old["number"], old["status"], old["void_reason"], old["replaced_by_number"], old["needs_sending"]), ("INV-2026-00001", "voided", "Wrong bill-to address", "INV-2026-00002", False))
        self.assertIsNotNone(old["voided_at"])
        self.assertEqual((new["number"], new["status"], new["replaces_number"], new["needs_sending"], new["custom_number"], new["total"], new["issued_at"]), ("INV-2026-00002", "active", "INV-2026-00001", True, False, "121.00", "2026-07-02"))
        self.assertEqual(new["due_date"], due.grace_until.isoformat())
        self.assertEqual((note["number"], note["kind"], note["net"], note["vat"], note["total"], note["vat_rate"], note["reverse_charge"]), ("CN-2026-00001", "void", "100.00", "21.00", "121.00", "21.00", False))
        self.assertEqual((note["invoice_number"], note["replacement_invoice_number"], note["applied_to"], note["due_id"]), ("INV-2026-00001", "INV-2026-00002", [], str(due.pk)))
        self.assertEqual(note["reason"], "Invoice voided and reissued: Wrong bill-to address")
        self.assertFalse(ClubCreditEntry.objects.exists())
        self.assertEqual(credit_svc.credit_balance(club), M("0"))
        got = Due.objects.get(pk=due.pk)
        self.assertEqual(got.invoice.number, "INV-2026-00002")
        self.assertEqual(got.invoices.count(), 2)

    def test_reverse_charge_credit_note_uses_the_invoices_own_flag(self):
        _club, due = self.billed("Dutch", country="NL", vat_id="NL123456789B01")
        note = self.void(due).json()["credit_note"]
        self.assertEqual((note["net"], note["vat"], note["total"], note["vat_rate"], note["reverse_charge"]), ("100.00", "0.00", "100.00", "0.00", True))

    def test_credit_note_follows_the_voided_invoice_not_a_changed_vat_rate(self):
        _club, due = self.billed()
        s = BillingSettings.current()
        s.vat_rate = M("6.00")
        s.save()
        cache.clear()
        data = self.void(due).json()
        self.assertEqual((data["credit_note"]["total"], data["credit_note"]["vat_rate"]), ("121.00", "21.00"))
        self.assertEqual(data["invoice"]["total"], "121.00")  # the replacement carries the Due's frozen amounts

    def test_zero_invoice_voids_without_a_credit_note(self):
        free = Plan.objects.create(code="free-v", name="Free V")
        PlanPrice.objects.create(plan=free, active_from=D(2000, 1, 1), amount=0)
        _club, due = self.billed(plan=free)
        before = counts()
        r = self.void(due)
        self.assertEqual(r.status_code, 201, r.content)
        data = r.json()
        self.assertIsNone(data["credit_note"])
        self.assertEqual((data["voided"]["status"], data["invoice"]["number"], data["invoice"]["needs_sending"], data["invoice"]["total"]), ("voided", "INV-2026-00002", False, "0.00"))
        self.assertEqual(CreditNote.objects.count(), 0)
        self.assertEqual(counts()["credit_seq"], before["credit_seq"])

    def test_voided_invoice_is_kept_unsendable_and_numbering_stays_gapless(self):
        _club, due = self.billed()
        self.void(due)
        old = Invoice.objects.get(number="INV-2026-00001")
        self.assertTrue(old.is_voided)
        with self.assertRaises(BillingError):
            invoices_svc.send_invoice(old)
        with self.assertRaises(BillingError):
            invoices_svc.mark_invoice_sent_manually(old)
        old.refresh_from_db()
        self.assertIsNone(old.sent_at)
        # the API routes act on the current invoice, which is the replacement
        r = self.post(f"/billing/dues/{due.pk}/invoice/mark-sent")
        self.assertEqual(r.json()["number"], "INV-2026-00002")
        other, _ = self.billed("Next")
        self.assertEqual(Due.objects.get(club=other).invoice.number, "INV-2026-00003")
        self.assertEqual(InvoiceSequence.objects.get(scope="2026").last_number, 3)

    def test_failed_attempt_burns_no_number_and_changes_nothing(self):
        club, due = self.billed()
        before, pos = counts(), self.position(club)
        with mock.patch.object(CreditNote.objects, "create", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
            reissue_svc.void_and_reissue(due.invoice, reason="x", actor=self.staff)
        self.assertEqual(counts(), before)
        self.assertEqual(self.position(club), pos)
        self.assertFalse(Invoice.objects.get(number="INV-2026-00001").is_voided)
        data = self.void(due).json()
        self.assertEqual((data["invoice"]["number"], data["credit_note"]["number"]), ("INV-2026-00002", "CN-2026-00001"))

    def test_custom_replacement_number(self):
        _club, due = self.billed()
        before = counts()
        for bad, status, code in (
            ("   ", 422, "validation_error"),
            ("X" * 33, 422, "validation_error"),
            ("INV-2026-00099", 422, "validation_error"),
            ("INV-2026-00001", 422, "validation_error"),
        ):  # the voided invoice's own number is in the reserved format
            r = self.void(due, number=bad)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (status, code), bad)
            self.assertEqual(counts(), before, bad)
        r = self.void(due, number="  EXT-77 ")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()["invoice"]["number"], r.json()["invoice"]["custom_number"]), ("EXT-77", True))
        self.assertEqual(InvoiceSequence.objects.get(scope="2026").last_number, 1)  # the counter is untouched
        # the voided number can never be reused, and neither can the custom one
        _club2, due2 = self.billed("Two")
        self.assertEqual(self.void(due2, number="EXT-77").status_code, 409)

    def test_reason_is_required(self):
        _club, due = self.billed()
        before = counts()
        for body in ({}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 301}):
            r = self.post(f"/billing/dues/{due.pk}/invoice/void-and-reissue", body)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "validation_error"), body)
        self.assertEqual(counts(), before)

    def test_refusals_change_nothing(self):
        club, due = self.billed(paid=M("121.00"))
        self.change(club, self.big)  # plan change: the old invoice now has a credit note
        before = counts()
        r = self.void(due)
        self.assertEqual((r.status_code, r.json()["error"]["code"], r.json()["error"]["message"]), (409, "billing_error", "This invoice has credit notes; it cannot be voided."))
        self.assertEqual(counts(), before)

        _c2, d2 = self.billed("Cancelled")
        Due.objects.filter(pk=d2.pk).update(status=Due.CANCELLED)
        before = counts()
        r = self.void(d2)
        self.assertEqual((r.status_code, r.json()["error"]["message"]), (409, "This period was cancelled; its invoice cannot be voided."))
        self.assertEqual(counts(), before)

        c3, d3 = self.billed("Twice")
        first = d3.invoice
        self.assertEqual(self.void(d3).status_code, 201)
        before = counts()
        with self.assertRaisesMessage(BillingError, "was already voided (replaced by INV-"):
            reissue_svc.void_and_reissue(first, reason="again", actor=self.staff)
        self.assertEqual(counts(), before)

        nothing = Due.objects.create(club=c3, plan=self.plan, amount=M("10.00"), period_start=D(2030, 1, 1))
        r = self.void(nothing)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "billing_error"))

    def test_a_reissued_period_can_still_have_its_plan_changed(self):
        club, due = self.billed(paid=M("121.00"))
        self.void(due)
        result = self.change(club, self.big)
        self.assertEqual(result.credit_notes[0].invoice.number, "INV-2026-00002")
        self.assert_conserved(club)

    def test_due_out_history_search_and_current_invoice_endpoints(self):
        club, due = self.billed()
        self.void(due, reason="First")
        self.void(due, reason="Second", number="EXT-2")
        out = self.get(f"/billing/dues/{due.pk}").json()
        self.assertEqual(out["invoice"]["number"], "EXT-2")
        self.assertEqual(out["invoice"]["replaces_number"], "INV-2026-00002")
        hist = out["voided_invoices"]
        self.assertEqual([h["number"] for h in hist], ["INV-2026-00002", "INV-2026-00001"])  # newest first
        self.assertEqual((hist[0]["replaced_by_number"], hist[0]["void_reason"], hist[0]["credit_note_number"]), ("EXT-2", "Second", "CN-2026-00002"))
        self.assertEqual((hist[1]["replaced_by_number"], hist[1]["credit_note_number"]), ("INV-2026-00002", "CN-2026-00001"))
        self.assertEqual(self.get(f"/billing/dues/{due.pk}/invoice").json()["number"], "EXT-2")
        for term in ("INV-2026-00001", "EXT-2"):
            found = self.get(f"/billing/dues?status=all&search={term}").json()
            self.assertEqual([d["id"] for d in found["items"]], [str(due.pk)], term)
        self.assertEqual(self.get("/billing/dues?status=all&search=INV-2026-00002").json()["count"], 1)
        listed = self.get("/billing/credit-notes?search=EXT-2").json()
        self.assertEqual([n["number"] for n in listed["items"]], ["CN-2026-00002"])
        self.assertEqual({n["kind"] for n in self.get("/billing/credit-notes").json()["items"]}, {"void"})
        b = self.get(f"/clubs/{club.pk}/billing").json()
        self.assertEqual(len(b["dues"][0]["voided_invoices"]), 2)

    def test_metrics_exclude_voided_invoices(self):
        from controlpanel.stats import _invoices_to_send

        _club, due = self.billed()
        self.assertEqual(_invoices_to_send(None), 1)
        self.void(due)
        self.assertEqual(_invoices_to_send(None), 1)  # only the replacement
        self.assertEqual(self.get("/billing/summary").json()["invoices_to_send"], 1)
        self.post(f"/billing/dues/{due.pk}/invoice/mark-sent")
        self.assertEqual(_invoices_to_send(None), 0)
        self.assertEqual(self.get("/billing/summary").json()["invoices_to_send"], 0)

    def test_idempotent_replay(self):
        _club, due = self.billed()
        h = {"Idempotency-Key": "void-1"}
        first, second = self.void(due, headers=h), self.void(due, headers=h)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json(), second.json())
        self.assertEqual(Invoice.objects.filter(due=due).count(), 2)
        self.assertEqual(CreditNote.objects.count(), 1)

    def test_audit_entry(self):
        club, due = self.billed()
        self.void(due, reason="Typo in name", number="EXT-9")
        entry = PlatformAuditLog.objects.get(action="billing.invoice_voided")
        self.assertEqual(entry.data, {"old_number": "INV-2026-00001", "new_number": "EXT-9", "credit_note": "CN-2026-00001", "reason": "Typo in name", "custom_number": True})
        self.assertEqual((entry.target_type, entry.target_id, entry.club_id), ("due", str(due.pk), club.pk))

    def test_pdfs_for_voided_and_replacement_invoices(self):
        _club, due = self.billed()
        self.void(due, reason="Wrong address")
        old, new = Invoice.objects.get(number="INV-2026-00001"), Invoice.objects.get(number="INV-2026-00002")
        html = invoices_svc.invoice_html(old)
        self.assertIn("VOID", html)
        self.assertIn("Voided 2026-", html)
        self.assertIn("replaced by INV-2026-00002; credit note CN-2026-00001", html)
        self.assertNotIn("Balance due", html)
        html = invoices_svc.invoice_html(new)
        self.assertNotIn("VOID", html)
        self.assertIn("Replaces invoice INV-2026-00001", html)
        note_html = credit_notes_svc.credit_note_html(CreditNote.objects.get())
        self.assertIn("replaced by invoice INV-2026-00002", note_html)
        self.assertIn("balance is unchanged", note_html)
        try:
            import weasyprint  # noqa: F401
        except ImportError, OSError:
            for path in (f"/billing/dues/{due.pk}/invoice.pdf", f"/billing/dues/{due.pk}/invoice.pdf?number=INV-2026-00001"):
                r = self.get(path)
                self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "billing_error"))
        else:
            cur = self.get(f"/billing/dues/{due.pk}/invoice.pdf")
            self.assertEqual((cur.status_code, cur.content[:4]), (200, b"%PDF"))
            self.assertIn("INV-2026-00002.pdf", cur["Content-Disposition"])
            gone = self.get(f"/billing/dues/{due.pk}/invoice.pdf?number=INV-2026-00001")
            self.assertEqual((gone.status_code, gone.content[:4]), (200, b"%PDF"))
            self.assertIn("INV-2026-00001.pdf", gone["Content-Disposition"])
        self.assertEqual(self.get(f"/billing/dues/{due.pk}/invoice.pdf?number=NOPE").status_code, 404)


# --- configurable number patterns ----------------------------------------------------------------------------------

from .services import numbering  # noqa: E402


def set_patterns(invoice=None, credit_note=None):
    row = BillingSettings.current()
    if invoice is not None:
        row.invoice_number_pattern = invoice
    if credit_note is not None:
        row.credit_note_number_pattern = credit_note
    row.save()


from controlpanel.api.schemas import IssuerSettingsIO  # noqa: E402


class PatternTests(TestCase):
    def test_valid_patterns(self):
        for ok in ("INV-{YYYY}-{NNNNN}", "{YY}{MM}-{NNN}", "FAC/{YYYY}/{NNNN}", "{N}", "Nr {N}", "A_B.C#{NN}", "{YYYY}{MM}{NNNNNN}"):
            self.assertEqual(numbering.validate_pattern(ok), ok)

    def test_invalid_patterns_have_clear_messages(self):
        cases = {
            "": "Enter a pattern",
            "   ": "Enter a pattern",
            "INV-{YYYY}": "needs a sequence",
            "INV-{N}-{NNN}": "only one sequence",
            "INV-{FOO}-{N}": "Unknown placeholder {FOO}",
            "INV-{yyyy}-{N}": "Unknown placeholder {yyyy}",
            "INV-{}-{N}": "Unknown placeholder {}",
            "INV-{N": "Braces",
            "INV}-{N}": "Braces",
            "INV-é-{N}": "not allowed",
            "INV:{N}": "not allowed",
            "INV*{N}": "not allowed",
            "X" * 36 + "{NNNN}": "at most 40",
            "X" * 25 + "{YYYY}-{NNNNN}": "too long",
        }
        for pattern, fragment in cases.items():
            with self.assertRaises(ValueError, msg=pattern) as cm:
                numbering.validate_pattern(pattern)
            self.assertIn(fragment, str(cm.exception), pattern)

    def test_render(self):
        d = D(2026, 3, 9)
        self.assertEqual(numbering.render("INV-{YYYY}-{NNNNN}", 1, d), "INV-2026-00001")
        self.assertEqual(numbering.render("{YY}{MM}-{N}", 7, d), "2603-7")
        self.assertEqual(numbering.render("FAC/{YYYY}/{NNNN}", 7, d), "FAC/2026/0007")
        self.assertEqual(numbering.render("{NN}", 123, d), "123")  # never cut
        self.assertEqual(numbering.render("{N}", 5, D(2026, 12, 31)), "5")

    def test_scope_and_resets(self):
        d = D(2026, 11, 5)
        self.assertEqual((numbering.scope_key("X{MM}{YY}-{N}", d), numbering.resets("X{MM}{YY}-{N}")), ("2026-11", "monthly"))
        self.assertEqual((numbering.scope_key("INV-{YYYY}-{N}", d), numbering.resets("INV-{YYYY}-{N}")), ("2026", "yearly"))
        self.assertEqual((numbering.scope_key("{YY}/{N}", d), numbering.resets("{YY}/{N}")), ("2026", "yearly"))
        self.assertEqual((numbering.scope_key("N-{NNN}", d), numbering.resets("N-{NNN}")), ("all", "never"))

    def test_matches_pattern(self):
        self.assertTrue(numbering.matches_pattern("INV-{YYYY}-{NNNNN}", "INV-2026-00001"))
        self.assertTrue(numbering.matches_pattern("INV-{YYYY}-{NNNNN}", "INV-1999-1234567"))
        self.assertFalse(numbering.matches_pattern("INV-{YYYY}-{NNNNN}", "INV-2026-001"))  # narrower than the padding
        self.assertFalse(numbering.matches_pattern("INV-{YYYY}-{NNNNN}", "XINV-2026-00001"))
        self.assertTrue(numbering.matches_pattern("FAC/{YYYY}/{N}", "FAC/2026/7"))
        self.assertTrue(numbering.matches_pattern("{YY}{MM}-{NN}", "2611-07"))
        self.assertFalse(numbering.matches_pattern("{YY}{MM}-{NN}", "2611-7"))
        self.assertTrue(numbering.matches_pattern("A.B{N}", "A.B12"))
        self.assertFalse(numbering.matches_pattern("A.B{N}", "AxB12"))  # the dot is literal


class NumberingTests(BillingTestBase):
    def invoice_on(self, day, name):
        club = make_club(name, country="BE")
        with at(day):
            return self.open(club, start=day).invoice

    def test_defaults_reproduce_the_original_format(self):
        row = BillingSettings.current()
        self.assertEqual((row.invoice_number_pattern, row.credit_note_number_pattern), ("INV-{YYYY}-{NNNNN}", "CN-{YYYY}-{NNNNN}"))
        self.assertEqual(self.invoice_on(D(2026, 5, 1), "A").number, "INV-2026-00001")
        self.assertEqual(self.invoice_on(D(2026, 5, 2), "B").number, "INV-2026-00002")
        self.assertEqual(self.invoice_on(D(2027, 1, 2), "C").number, "INV-2027-00001")  # yearly reset

    def test_yearly_monthly_and_never_scopes(self):
        set_patterns(invoice="{YY}{MM}/{NNN}")
        self.assertEqual([self.invoice_on(d, n).number for d, n in ((D(2026, 1, 5), "a"), (D(2026, 1, 20), "b"), (D(2026, 2, 1), "c"), (D(2026, 2, 2), "d"), (D(2027, 1, 1), "e"))], ["2601/001", "2601/002", "2602/001", "2602/002", "2701/001"])
        self.assertEqual(sorted(InvoiceSequence.objects.values_list("scope", "last_number")), [("2026-01", 2), ("2026-02", 2), ("2027-01", 1)])
        set_patterns(invoice="Z{N}")
        self.assertEqual([self.invoice_on(D(2030, 1, 1) + datetime.timedelta(days=i), f"n{i}").number for i in range(2)], ["Z1", "Z2"])
        self.assertTrue(InvoiceSequence.objects.filter(scope="all").exists())

    def test_pattern_change_mid_year_keeps_the_counter(self):
        a = self.invoice_on(D(2026, 3, 1), "A").number
        set_patterns(invoice="FAC/{YYYY}/{NNNN}")
        b = self.invoice_on(D(2026, 3, 2), "B").number
        c = self.invoice_on(D(2026, 3, 3), "C").number
        self.assertEqual((a, b, c), ("INV-2026-00001", "FAC/2026/0002", "FAC/2026/0003"))
        self.assertEqual(InvoiceSequence.objects.get(scope="2026").last_number, 3)
        self.assertEqual(len({a, b, c}), 3)

    def test_collision_with_an_existing_number_skips_forward(self):
        club = make_club("Old", country="BE")
        due = Due.objects.create(club=club, plan=self.plan, amount=M("100.00"), period_start=START)
        Invoice.objects.create(due=due, number="REF-0002", custom_number=True, issued_at=START)
        set_patterns(invoice="REF-{NNNN}")
        self.assertEqual(self.invoice_on(D(2026, 4, 1), "A").number, "REF-0001")
        self.assertEqual(self.invoice_on(D(2026, 4, 2), "B").number, "REF-0003")  # REF-0002 is taken: skipped, never duplicated
        self.assertEqual(InvoiceSequence.objects.get(scope="all").last_number, 3)

    def test_rollback_burns_nothing_under_a_pattern(self):
        set_patterns(invoice="X{YY}-{NNN}")
        club = make_club("Rolled", country="BE")
        due = Due.objects.create(club=club, plan=self.plan, amount=M("100.00"), period_start=START)
        with at(D(2026, 6, 1)), mock.patch.object(Invoice.objects, "create", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
            invoices_svc.issue_invoice(due)
        with at(D(2026, 6, 1)):
            self.assertEqual(invoices_svc.issue_invoice(due).number, "X26-001")

    def test_credit_notes_use_their_own_pattern_and_counter(self):
        set_patterns(credit_note="CR{YY}{MM}-{NN}")
        club = make_club("Cn", country="BE")
        with at(D(2026, 1, 1)):
            invoice = self.open(club).invoice
        from django.db import transaction

        with at(D(2026, 7, 2)), transaction.atomic():
            n1 = credit_notes_svc.issue_credit_note(invoice, net=M("10.00"), vat=M("2.10"), reason="r", credited_from=START, credited_to=START).number
            n2 = credit_notes_svc.issue_credit_note(invoice, net=M("10.00"), vat=M("2.10"), reason="r", credited_from=START, credited_to=START).number
        self.assertEqual((n1, n2), ("CR2607-01", "CR2607-02"))
        self.assertEqual(list(CreditNoteSequence.objects.values_list("scope", "last_number")), [("2026-07", 2)])
        self.assertEqual(invoice.number, "INV-2026-00001")  # a separate counter

    def test_allocation_needs_a_transaction(self):
        # TestCase wraps in a transaction, so simulate the check through the guard directly
        with mock.patch("billing.services.numbering.transaction.get_connection") as conn:
            conn.return_value.in_atomic_block = False
            with self.assertRaises(RuntimeError):
                numbering.allocate(InvoiceSequence, "INV-{YYYY}-{NNNNN}", START, lambda n: False)

    def test_reserved_numbers(self):
        club = make_club("Res", country="BE")
        due = Due.objects.create(club=club, plan=self.plan, amount=M("100.00"), period_start=START)
        set_patterns(invoice="FAC/{YYYY}/{NNNN}")
        for bad in ("FAC/2026/0007", "FAC/1999/12345", "INV-2026-00005", "INV-1999-1"):  # current pattern and the legacy format
            with self.assertRaises(ValueError, msg=bad) as cm:
                invoices_svc.clean_number(bad)
            self.assertIn("FAC/{YYYY}/{NNNN}", str(cm.exception))
        self.assertEqual(invoices_svc.clean_number(" FAC-2026-7 "), "FAC-2026-7")  # different separators: fine
        self.assertEqual(invoices_svc.issue_invoice(due, number="EXT-1").number, "EXT-1")

    def test_old_numbers_are_untouched_by_a_pattern_change(self):
        a = self.invoice_on(D(2026, 3, 1), "A")
        set_patterns(invoice="ZZ{N}")
        a.refresh_from_db()
        self.assertEqual(a.number, "INV-2026-00001")


class NumberingApiTests(ApiBase):
    def put_settings(self, **changes):
        body = IssuerSettingsIO.of(BillingSettings.current()).dict()
        for k in IssuerSettingsIO.model_fields:
            if k in ("invoice_next_sequence", "credit_note_next_sequence"):
                body[k] = None
        body.update(changes)
        with at(SWITCH):
            return self.client.put("/billing/settings", json=body)

    def test_get_settings_exposes_patterns_and_next_numbers(self):
        data = self.get("/billing/settings").json()
        self.assertEqual((data["invoice_number_pattern"], data["credit_note_number_pattern"]), ("INV-{YYYY}-{NNNNN}", "CN-{YYYY}-{NNNNN}"))
        self.assertEqual((data["invoice_next_number_preview"], data["credit_note_next_number_preview"]), ("INV-2026-00001", "CN-2026-00001"))
        self.assertEqual((data["invoice_current_sequence"], data["invoice_counter_resets"], data["invoice_next_sequence"]), (0, "yearly", None))
        self.billed()
        data = self.get("/billing/settings").json()
        self.assertEqual((data["invoice_next_number_preview"], data["invoice_current_sequence"]), ("INV-2026-00002", 1))

    def test_put_round_trip_and_audit(self):
        r = self.put_settings(invoice_number_pattern=" FAC/{YYYY}/{NNNN} ", credit_note_number_pattern="CR-{YY}{MM}-{N}")
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertEqual((data["invoice_number_pattern"], data["credit_note_number_pattern"]), ("FAC/{YYYY}/{NNNN}", "CR-{YY}{MM}-{N}"))
        self.assertEqual((data["invoice_next_number_preview"], data["credit_note_next_number_preview"], data["credit_note_counter_resets"]), ("FAC/2026/0001", "CR-2607-1", "monthly"))
        row = BillingSettings.objects.get()
        self.assertEqual(row.invoice_number_pattern, "FAC/{YYYY}/{NNNN}")
        event = PlatformAuditLog.objects.filter(action="billing.issuer_settings_updated").latest("created")
        self.assertEqual(event.data["changed"]["invoice_number_pattern"], ["INV-{YYYY}-{NNNNN}", "FAC/{YYYY}/{NNNN}"])
        self.assertNotIn("invoice_next_number_preview", event.data["changed"])

    def test_older_clients_without_the_new_fields_keep_the_patterns(self):
        set_patterns(invoice="FAC/{YYYY}/{NNNN}")
        body = IssuerSettingsIO.of(BillingSettings.current()).dict()
        for k in ("invoice_number_pattern", "credit_note_number_pattern", "invoice_next_sequence", "credit_note_next_sequence"):
            body.pop(k)
        with at(SWITCH):
            r = self.client.put("/billing/settings", json=body)
        self.assertEqual(r.status_code, 200, r.content)
        # full replacement: omitted patterns fall back to the defaults (documented), the counters are untouched
        self.assertEqual(r.json()["invoice_number_pattern"], "INV-{YYYY}-{NNNNN}")

    def test_invalid_patterns_are_field_errors(self):
        r = self.put_settings(invoice_number_pattern="INV-{FOO}-{N}", credit_note_number_pattern="CN-{YYYY}")
        self.assertEqual(r.status_code, 422)
        fields = r.json()["error"]["fields"]
        self.assertIn("Unknown placeholder {FOO}", fields["invoice_number_pattern"][0])
        self.assertIn("needs a sequence", fields["credit_note_number_pattern"][0])
        self.assertEqual(BillingSettings.objects.get().invoice_number_pattern, "INV-{YYYY}-{NNNNN}")

    def test_next_number_jump_forward_and_never_back(self):
        self.billed()  # counter at 1
        r = self.put_settings(invoice_number_pattern="FAC/{YYYY}/{NNNN}", invoice_next_sequence=7, credit_note_next_sequence=40)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["invoice_next_number_preview"], r.json()["credit_note_next_number_preview"]), ("FAC/2026/0007", "CN-2026-00040"))
        self.assertEqual(InvoiceSequence.objects.get(scope="2026").last_number, 6)
        _club, due = self.billed("Next")
        self.assertEqual(due.invoice.number, "FAC/2026/0007")
        self.assertEqual(self.get("/billing/settings").json()["invoice_next_number_preview"], "FAC/2026/0008")
        for value in (3, 8, 1):  # at 7 now: 8 is the very next number (allowed), everything at or below 7 is refused
            r = self.put_settings(invoice_next_sequence=value)
            if value == 8:
                self.assertEqual(r.status_code, 200, r.content)
            else:
                self.assertEqual(r.status_code, 422, value)
                self.assertEqual(r.json()["error"]["fields"]["invoice_next_sequence"], ["Cannot go back: the sequence is already at 7."])
        self.assertEqual(InvoiceSequence.objects.get(scope="2026").last_number, 7)
        r = self.put_settings(invoice_next_sequence=0)
        self.assertEqual(r.status_code, 422)

    def test_jump_is_validated_against_the_new_patterns_scope(self):
        r = self.put_settings(invoice_number_pattern="{YY}{MM}-{NN}", invoice_next_sequence=5)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["invoice_next_number_preview"], "2607-05")
        self.assertEqual(list(InvoiceSequence.objects.values_list("scope", "last_number")), [("2026-07", 4)])

    def test_a_bad_field_changes_nothing_not_even_the_jump(self):
        r = self.put_settings(invoice_next_sequence=9, vat_rate="150.00")
        self.assertEqual(r.status_code, 422)
        self.assertFalse(InvoiceSequence.objects.exists())

    def test_numbering_preview_endpoint(self):
        self.billed()
        r = self.post("/billing/numbering/preview", {"invoice_number_pattern": "FAC/{YYYY}/{NNNN}", "credit_note_number_pattern": "CR-{YY}{MM}-{N}"})
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertEqual(data["invoice"], {"valid": True, "error": None, "example": "FAC/2026/0001", "next": "FAC/2026/0002", "scope": "2026", "scope_label": "2026", "resets": "yearly"})
        self.assertEqual(data["credit_note"], {"valid": True, "error": None, "example": "CR-2607-1", "next": "CR-2607-1", "scope": "2026-07", "scope_label": "July 2026", "resets": "monthly"})
        self.assertEqual({p["token"] for p in data["placeholders"]}, {"{YYYY}", "{YY}", "{MM}", "{N}", "{NNNNN}"})
        self.assertEqual(data["max_length"], 40)
        never = self.post("/billing/numbering/preview", {"invoice_number_pattern": "Z{N}", "date": "2027-02-03"}).json()["invoice"]
        self.assertEqual((never["example"], never["next"], never["scope"], never["scope_label"], never["resets"]), ("Z1", "Z1", "all", "All time", "never"))
        monthly = self.post("/billing/numbering/preview", {"invoice_number_pattern": "{YY}{MM}-{NN}", "date": "2027-02-03"}).json()["invoice"]
        self.assertEqual((monthly["example"], monthly["scope"]), ("2702-01", "2027-02"))
        bad = self.post("/billing/numbering/preview", {"invoice_number_pattern": "INV-{FOO}"}).json()
        self.assertEqual(bad["invoice"]["valid"], False)
        self.assertIn("Unknown placeholder {FOO}", bad["invoice"]["error"])
        self.assertIsNone(bad["invoice"]["example"])
        self.assertTrue(bad["credit_note"]["valid"])  # the saved credit note pattern
        before = counts()
        self.post("/billing/numbering/preview", {})
        self.assertEqual(counts(), before)
        self.assertEqual(BillingSettings.objects.get().invoice_number_pattern, "INV-{YYYY}-{NNNNN}")


class CustomInvoiceNumberTests(ApiBase):
    def subscribe(self, club, **extra):
        return self.post(f"/clubs/{club.pk}/billing/subscribe", {"plan_id": str(self.plan.pk), **extra}, day=START)

    def fresh_club(self, name):
        return make_club(name, country="BE", billed=False)

    def test_subscribe_with_a_custom_number(self):
        club = self.fresh_club("Sub")
        r = self.subscribe(club, invoice_number="  OLD-77 ")
        self.assertEqual(r.status_code, 201, r.content)
        inv = Invoice.objects.get(due__club=club)
        self.assertEqual((inv.number, inv.custom_number), ("OLD-77", True))
        self.assertFalse(InvoiceSequence.objects.exists())  # the automatic counter was not consumed
        self.assertEqual(PlatformAuditLog.objects.get(action="billing.subscribed").data["invoice_number"], "OLD-77")

    def test_trial_zero_invoice_takes_the_custom_number(self):
        club = self.fresh_club("Trial")
        r = self.subscribe(club, trial_days=14, invoice_number="TRIAL-1")
        self.assertEqual(r.status_code, 201, r.content)
        inv = Invoice.objects.get(due__club=club)
        self.assertEqual((inv.number, inv.due.is_trial, inv.due.total), ("TRIAL-1", True, M("0")))

    def test_bad_custom_number_changes_nothing(self):
        club = self.fresh_club("Bad")
        before = counts()
        for bad in ("   ", "X" * 33, "INV-2026-00077", "INV-1999-1"):
            r = self.subscribe(club, invoice_number=bad)
            self.assertEqual(r.status_code, 422, bad)
            self.assertIn("invoice_number", r.json()["error"]["fields"], bad)
            self.assertEqual(counts(), before, bad)
        r = self.subscribe(club, invoice_number="TAKEN-1")
        self.assertEqual(r.status_code, 201)
        club2 = self.fresh_club("Bad2")
        r = self.subscribe(club2, invoice_number="TAKEN-1")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "billing_error"))
        self.assertFalse(ClubBilling.objects.filter(club=club2).exists())
        self.assertFalse(Subscription.objects.filter(club=club2).exists())

    def test_voided_numbers_count_as_in_use(self):
        _club, due = self.billed("Voided")
        self.post(f"/billing/dues/{due.pk}/invoice/void-and-reissue", {"reason": "x", "number": "GONE-1"})
        club2 = self.fresh_club("Reuse")
        gone = Invoice.objects.get(number="INV-2026-00001")
        Invoice.objects.filter(pk=gone.pk).update(number="OLDVOID-1")
        r = self.subscribe(club2, invoice_number="OLDVOID-1")
        self.assertEqual(r.status_code, 409)

    def test_open_next_period_with_a_custom_number_and_renewal_stays_automatic(self):
        club, _due = self.billed("Per")
        r = self.post(f"/clubs/{club.pk}/billing/periods", {"invoice_number": "NEXT-1"}, day=SWITCH)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Invoice.objects.get(number="NEXT-1").custom_number, True)
        self.assertEqual(InvoiceSequence.objects.get(scope="2026").last_number, 1)
        before = counts()
        bad = self.post(f"/clubs/{club.pk}/billing/periods", {"invoice_number": "INV-2026-00002"}, day=SWITCH)
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.json()["error"]["fields"]["invoice_number"][0][:20], "This format is reser")
        self.assertEqual(counts(), before)
        # the unattended renewal job never takes a custom number
        renewed = dues_svc.renew(ClubBilling.objects.get(club=club))
        self.assertEqual((renewed.invoice.number, renewed.invoice.custom_number), ("INV-2026-00002", False))

    def test_change_plan_custom_number_and_preview_echo(self):
        club, _due = self.billed("Chg", paid=M("121.00"))
        base = f"/clubs/{club.pk}/billing/change-plan"
        pre = self.post(base + "/preview", {"plan_id": str(self.big.pk)}).json()
        self.assertEqual(pre["invoice_number"], {"mode": "automatic", "number": None, "next_automatic": "INV-2026-00002"})
        pre = self.post(base + "/preview", {"plan_id": str(self.big.pk), "invoice_number": " PC-9 "}).json()
        self.assertEqual(pre["invoice_number"], {"mode": "custom", "number": "PC-9", "next_automatic": "INV-2026-00002"})
        before = counts()
        for path in (base + "/preview", base):  # validation problems show in the preview and block the change
            r = self.post(path, {"plan_id": str(self.big.pk), "invoice_number": "INV-2026-00123"})
            self.assertEqual((r.status_code, list(r.json()["error"]["fields"])), (422, ["invoice_number"]), path)
            r = self.post(path, {"plan_id": str(self.big.pk), "invoice_number": "INV-1999-1"})
            self.assertEqual(r.status_code, 422, path)
        self.assertEqual(counts(), before)
        done = self.post(base, {"plan_id": str(self.big.pk), "invoice_number": "PC-9"})
        self.assertEqual(done.status_code, 201, done.content)
        self.assertEqual(done.json()["invoice_number"]["mode"], "custom")
        self.assertEqual(done.json()["created"]["new_invoice_number"], "PC-9")
        self.assertEqual(done.json()["credit_notes"][0]["number"], "CN-2026-00001")  # credit notes stay automatic
        self.assertEqual(InvoiceSequence.objects.get(scope="2026").last_number, 1)

    def test_change_plan_number_in_use_is_a_conflict_and_changes_nothing(self):
        club, _due = self.billed("Chg2", paid=M("121.00"))
        self.billed("Other")
        Invoice.objects.filter(number="INV-2026-00002").update(number="USED-1", custom_number=True)
        before = counts()
        r = self.post(f"/clubs/{club.pk}/billing/change-plan", {"plan_id": str(self.big.pk), "invoice_number": "USED-1"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "billing_error"))
        self.assertEqual(counts(), before)

    def test_numbers_follow_the_pattern_everywhere(self):
        set_patterns(invoice="FAC/{YYYY}/{NNNN}", credit_note="NC-{N}")
        club, due = self.billed("Pat", paid=M("121.00"))
        self.assertEqual(due.invoice.number, "FAC/2026/0001")
        done = self.post(f"/clubs/{club.pk}/billing/change-plan", {"plan_id": str(self.big.pk)}).json()
        self.assertEqual((done["created"]["new_invoice_number"], done["credit_notes"][0]["number"]), ("FAC/2026/0002", "NC-1"))
