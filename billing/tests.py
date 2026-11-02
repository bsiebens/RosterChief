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

    def test_zero_amount_has_no_vat_and_is_paid(self):
        free = Plan.objects.create(code="free-test", name="Free")
        PlanPrice.objects.create(plan=free, active_from=datetime.date(2000, 1, 1), amount=0)
        due = self.open(make_club("Freebie", country="BE"), plan=free)
        self.assertEqual((due.vat_amount, due.status), (Decimal("0"), Due.PAID))

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
        self.assertFalse(hasattr(self.plan.terms, "is_trial"))
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
