import datetime
import sys
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.utils import IntegrityError
from django.test import TestCase
from django.utils import timezone

from authentication.models import User
from club.models import Club, ClubRole
from members.models import Member

from .models import DEFAULT_DURATION_MONTHS, DEFAULT_GRACE_DAYS, DEFAULT_RENEWAL_LEAD_DAYS, Due, Invoice, Plan, PlanPrice, Subscription, add_months
from .services import BillingError
from .services.dues import archivable_clubs, dues_in_grace, dues_overdue, next_period_start, open_period, reactivate, record_payment, remove_payment, renew, start_trial, subscribe, subscriptions_due_for_renewal, waive
from .services.invoices import invoice_pdf, issue_invoice, render_pdf
from .services.notices import club_billing_notice
from .services.plans import delete_plan, plan_deletion_impact
from .services.reminders import admin_emails, reminders_to_send, send_reminder


class BillingTestBase(TestCase):
    # setUpTestData, not setUp: the club and the priced plan are read-only scaffolding for
    # every subclass, so they are built once per class. Django hands each test its own deep
    # copy and rolls the database back afterwards, so the tests that archive the club or
    # soft-delete the plan still start from a clean slate.
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.club = Club.objects.create(name="Ajax United")
        cls.plan = Plan.objects.create(name="Standard")
        # Priced well back, so a backdated (lapsed) period still has a price in force —
        # opening one before any price existed is refused, and rightly so.
        PlanPrice.objects.create(plan=cls.plan, active_from=cls.today - datetime.timedelta(days=1200), amount=Decimal("500.00"))

    def bill(self, start=None, club=None):
        return open_period(club or self.club, start=start, plan=self.plan)


class PlanPriceTests(BillingTestBase):
    def test_the_price_in_force_is_the_latest_one_that_has_started(self):
        PlanPrice.objects.create(plan=self.plan, active_from=self.today, amount=Decimal("600.00"))

        self.assertEqual(self.plan.price_on(self.today - datetime.timedelta(days=1)), Decimal("500.00"))
        self.assertEqual(self.plan.price_on(self.today), Decimal("600.00"))

    def test_a_future_price_does_not_apply_yet(self):
        PlanPrice.objects.create(plan=self.plan, active_from=self.today + datetime.timedelta(days=30), amount=Decimal("600.00"))

        self.assertEqual(self.plan.price_on(self.today), Decimal("500.00"))

    def test_a_plan_with_no_price_yet_cannot_be_billed(self):
        # None must never be read as free.
        empty = Plan.objects.create(name="Enterprise")

        self.assertIsNone(empty.price_on(self.today))

        with self.assertRaises(BillingError):
            open_period(self.club, plan=empty)


class PeriodTests(BillingTestBase):
    def test_a_period_runs_for_the_plans_duration(self):
        due = self.bill(start=datetime.date(2026, 3, 1))

        self.assertEqual(due.period_end, datetime.date(2027, 2, 28))

    def test_grace_is_measured_from_the_period_start_not_its_end(self):
        # The whole point of the redesign: measured from the end, an annual club would get
        # ~410 days of unpaid use before anything switched it off.
        due = self.bill(start=datetime.date(2026, 3, 1))

        self.assertEqual(due.grace_until, datetime.date(2026, 3, 1) + datetime.timedelta(days=DEFAULT_GRACE_DAYS))
        self.assertLess(due.grace_until, due.period_end)

    def test_a_leap_day_period_does_not_explode(self):
        # 29 February has no counterpart in a common year.
        self.assertEqual(add_months(datetime.date(2028, 2, 29), 12), datetime.date(2029, 2, 28))

    def test_the_next_period_continues_from_the_last_one(self):
        # Not from today: a club that pays two months late has still used those two months,
        # and restarting the clock at the payment date would quietly gift them away.
        first = self.bill(start=self.today - datetime.timedelta(days=400))

        self.assertEqual(next_period_start(self.club), first.period_end + datetime.timedelta(days=1))

    def test_a_first_period_starts_today(self):
        self.assertEqual(next_period_start(self.club), self.today)

    def test_the_amount_is_snapshotted_at_the_price_of_the_day(self):
        due = self.bill()
        PlanPrice.objects.create(plan=self.plan, active_from=self.today + datetime.timedelta(days=1), amount=Decimal("900.00"))
        due.refresh_from_db()

        # Raising the rate must not rewrite what was already billed.
        self.assertEqual(due.amount, Decimal("500.00"))

    def test_a_club_cannot_be_billed_twice_for_one_period(self):
        self.bill(start=self.today)

        with self.assertRaises(BillingError):
            self.bill(start=self.today)

    def test_a_club_with_no_plan_cannot_be_billed(self):
        with self.assertRaises(BillingError):
            open_period(Club.objects.create(name="Feyenoord"))

    def test_subscribing_puts_a_club_on_a_plan_and_opens_a_period(self):
        club = Club.objects.create(name="Feyenoord")

        subscribe(club, self.plan)

        self.assertEqual(Subscription.objects.get(club=club).plan, self.plan)
        self.assertEqual(club.dues.count(), 1)


class PaymentTests(BillingTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.due = open_period(cls.club, plan=cls.plan)

    def test_a_part_payment_leaves_the_due_partially_paid(self):
        record_payment(self.due, Decimal("200.00"))
        self.due.refresh_from_db()

        self.assertEqual(self.due.status, Due.Status.PARTIAL)
        self.assertEqual(self.due.balance, Decimal("300.00"))
        self.assertIsNone(self.due.paid_at)

    def test_payments_accumulate_until_the_due_is_settled(self):
        record_payment(self.due, Decimal("200.00"))
        record_payment(self.due, Decimal("300.00"))
        self.due.refresh_from_db()

        self.assertEqual(self.due.status, Due.Status.PAID)
        self.assertEqual(self.due.balance, Decimal("0.00"))
        self.assertIsNotNone(self.due.paid_at)

    def test_an_overpayment_still_settles_the_due(self):
        record_payment(self.due, Decimal("600.00"))
        self.due.refresh_from_db()

        self.assertEqual(self.due.status, Due.Status.PAID)

    def test_removing_a_payment_re_derives_the_due(self):
        # amount_paid is summed from the payments, never incremented: an increment drifts the
        # moment one is deleted, and the drift still looks like money.
        first = record_payment(self.due, Decimal("200.00"))
        record_payment(self.due, Decimal("300.00"))

        remove_payment(first)
        self.due.refresh_from_db()

        self.assertEqual(self.due.amount_paid, Decimal("300.00"))
        self.assertEqual(self.due.status, Due.Status.PARTIAL)

    def test_removing_the_only_payment_puts_the_due_back_to_unpaid(self):
        payment = record_payment(self.due, Decimal("500.00"))

        remove_payment(payment)
        self.due.refresh_from_db()

        self.assertEqual(self.due.status, Due.Status.UNPAID)
        self.assertEqual(self.due.amount_paid, Decimal("0.00"))
        self.assertIsNone(self.due.paid_at)

    def test_a_zero_payment_is_refused(self):
        with self.assertRaises(BillingError):
            record_payment(self.due, Decimal("0.00"))

    def test_a_waived_period_cannot_take_a_payment(self):
        waive(self.due)

        with self.assertRaises(BillingError):
            record_payment(self.due, Decimal("100.00"))

    def test_a_period_with_payments_cannot_be_waived(self):
        record_payment(self.due, Decimal("100.00"))

        with self.assertRaises(BillingError):
            waive(self.due)

    def test_a_waived_period_owes_nothing_and_never_archives_a_club(self):
        waive(self.due)
        self.due.refresh_from_db()

        self.assertFalse(self.due.is_owing)
        self.assertFalse(self.due.is_overdue(self.due.grace_until + datetime.timedelta(days=1)))


class GraceAndArchiveTests(BillingTestBase):
    LAPSED = DEFAULT_GRACE_DAYS + 10

    def test_a_started_but_unpaid_period_inside_grace_is_in_grace(self):
        # Grace runs from the period START now, so this is a period that began a few days
        # ago and has not been paid -- not one that has already run its full length.
        due = self.bill(start=self.today - datetime.timedelta(days=5))

        self.assertTrue(due.is_in_grace(self.today))
        self.assertFalse(due.is_overdue(self.today))
        self.assertIn(due, dues_in_grace(self.today))

    def test_a_period_issued_ahead_of_its_start_is_not_yet_in_grace(self):
        due = self.bill(start=self.today + datetime.timedelta(days=10))

        self.assertTrue(due.is_issued_ahead(self.today))
        self.assertFalse(due.is_in_grace(self.today))
        self.assertFalse(due.is_overdue(self.today))

    def test_a_period_past_grace_is_overdue(self):
        due = self.bill(start=self.today - datetime.timedelta(days=self.LAPSED))

        self.assertTrue(due.is_overdue(self.today))
        self.assertFalse(due.is_in_grace(self.today))
        self.assertIn(due, dues_overdue(self.today))

    def test_a_paid_period_is_never_overdue(self):
        due = self.bill(start=self.today - datetime.timedelta(days=self.LAPSED))
        record_payment(due, Decimal("500.00"))
        due.refresh_from_db()

        self.assertFalse(due.is_overdue(self.today))
        self.assertNotIn(due, dues_overdue(self.today))

    def test_an_overdue_club_is_archivable(self):
        subscribe(self.club, self.plan, start=self.today - datetime.timedelta(days=self.LAPSED))

        self.assertEqual(archivable_clubs(self.today).count(), 1)

    def test_a_club_that_opted_out_is_never_archived(self):
        # auto_archive off is how you stop a club you are negotiating with from being
        # switched off overnight.
        subscribe(self.club, self.plan, start=self.today - datetime.timedelta(days=self.LAPSED), auto_archive=False)

        self.assertEqual(archivable_clubs(self.today).count(), 0)

    def test_an_already_archived_club_is_not_archived_again(self):
        subscribe(self.club, self.plan, start=self.today - datetime.timedelta(days=self.LAPSED))
        self.club.archive()

        self.assertEqual(archivable_clubs(self.today).count(), 0)


class ArchiveCommandTests(BillingTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        subscribe(cls.club, cls.plan, start=cls.today - datetime.timedelta(days=DEFAULT_GRACE_DAYS + 10))

    def run_command(self, *args):
        out = StringIO()
        call_command("archive_overdue_clubs", *args, stdout=out)
        return out.getvalue()

    def test_it_reports_without_archiving_by_default(self):
        # The asymmetry is the point: this switches off paying customers, so a cron
        # misconfiguration or a clock skew must cost an email, not a morning of angry clubs.
        output = self.run_command()

        self.club.refresh_from_db()
        self.assertFalse(self.club.is_archived)
        self.assertIn("Dry run", output)
        self.assertIn("Ajax United", output)

    def test_it_archives_with_commit(self):
        self.run_command("--commit")

        self.club.refresh_from_db()
        self.assertTrue(self.club.is_archived)

    def test_it_says_so_when_nothing_is_overdue(self):
        record_payment(self.club.dues.first(), Decimal("500.00"))

        self.assertIn("Nothing overdue", self.run_command())


class ReactivationTests(BillingTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Through subscribe(), not open_period(): reactivating reads the club's plan off its
        # subscription, and a club billed without one cannot be re-billed later.
        subscribe(cls.club, cls.plan, start=cls.today - datetime.timedelta(days=400))
        cls.first = cls.club.dues.first()
        cls.club.archive()

    def test_reactivating_continues_from_the_lapsed_period_by_default(self):
        due = reactivate(self.club)

        self.club.refresh_from_db()
        self.assertFalse(self.club.is_archived)
        self.assertEqual(due.period_start, self.first.period_end + datetime.timedelta(days=1))

    def test_a_chosen_start_forgives_the_gap(self):
        due = reactivate(self.club, start=self.today)

        self.assertEqual(due.period_start, self.today)


class InvoiceTests(BillingTestBase):
    def test_every_period_is_invoiced_when_it_opens(self):
        due = self.bill()

        self.assertTrue(Invoice.objects.filter(due=due).exists())

    def test_numbers_run_in_one_platform_wide_series(self):
        # Unlike the shop's per-club order numbers: these are OUR invoices, and one sequence
        # covers every club we bill.
        first = self.bill(start=self.today).invoice
        second = open_period(Club.objects.create(name="Feyenoord"), plan=self.plan).invoice

        year = timezone.now().year
        self.assertEqual(first.number, f"INV-{year}-00001")
        self.assertEqual(second.number, f"INV-{year}-00002")

    def test_re_issuing_does_not_burn_a_number(self):
        # A gap in an invoice series is a question you do not want to have to answer.
        due = self.bill()

        self.assertEqual(issue_invoice(due), due.invoice)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_the_invoice_renders_the_frozen_snapshot(self):
        due = self.bill()
        record_payment(due, Decimal("200.00"), reference="TRX-9")
        due.refresh_from_db()

        with mock.patch("billing.services.invoices.render_pdf", return_value=b"%PDF-fake") as renderer:
            invoice_pdf(due.invoice)

        html = renderer.call_args.args[0]
        self.assertIn("INV-", html)
        self.assertIn("Ajax United", html)
        self.assertIn("500.00", html)  # billed
        self.assertIn("200.00", html)  # paid
        self.assertIn("300.00", html)  # balance

    def test_the_invoice_is_billed_to_the_clubs_legal_name_when_set(self):
        self.club.legal_name = "Ajax United VZW"
        self.club.save(update_fields=["legal_name"])
        due = self.bill()

        with mock.patch("billing.services.invoices.render_pdf", return_value=b"%PDF-fake") as renderer:
            invoice_pdf(due.invoice)

        html = renderer.call_args.args[0]
        self.assertIn("Ajax United VZW", html)

    def test_the_pdf_library_is_only_needed_when_a_pdf_is_asked_for(self):
        # WeasyPrint binds to native pango/cairo. The app, the tests and every other page must
        # run without them; only this call may fail.
        with mock.patch.dict(sys.modules, {"weasyprint": mock.MagicMock()}):
            sys.modules["weasyprint"].HTML.return_value.write_pdf.return_value = b"%PDF-1.7"

            self.assertEqual(render_pdf("<p>hi</p>"), b"%PDF-1.7")

    def test_a_missing_pdf_library_says_what_is_missing(self):
        with mock.patch.dict(sys.modules, {"weasyprint": None}), self.assertRaises(BillingError) as caught:
            render_pdf("<p>hi</p>")

        self.assertIn("pango", str(caught.exception))


class ModelStringTests(BillingTestBase):
    def test_models_describe_themselves(self):
        due = self.bill()
        payment = record_payment(due, Decimal("10.00"))

        self.assertEqual(str(self.plan), "Standard")
        self.assertIn("500.00", str(self.plan.prices.first()))
        self.assertIn("Ajax United", str(due))
        self.assertIn("10.00", str(payment))
        self.assertIn("INV-", str(due.invoice))
        self.assertIn("Standard", str(subscribe(Club.objects.create(name="PSV"), self.plan)))


class RenewalTests(BillingTestBase):
    """The leak this closes: a club whose period lapses with its last due PAID owes nothing,
    so dues_overdue() is empty, so archive_overdue_clubs never fires — and the club keeps
    using the platform for free while every number on the dashboard stays green."""

    def ending_in(self, days, **kwargs):
        """A club whose current period ends `days` from now."""
        club = Club.objects.create(name=f"Club {days}")
        subscribe(club, self.plan, start=self.today - datetime.timedelta(days=365 - days), **kwargs)
        return club

    def test_a_club_nearing_its_end_date_is_picked_up(self):
        club = self.ending_in(20)

        due = [s.club for s in subscriptions_due_for_renewal()]

        self.assertIn(club, due)

    def test_a_club_with_a_period_beyond_the_horizon_is_left_alone(self):
        club = self.ending_in(200)

        self.assertNotIn(club, [s.club for s in subscriptions_due_for_renewal()])

    def test_renewing_continues_from_the_last_period(self):
        club = self.ending_in(20)
        first = club.dues.first()

        renew(club.subscription)

        latest = club.dues.order_by("-period_start").first()
        self.assertEqual(latest.period_start, first.period_end + datetime.timedelta(days=1))
        self.assertEqual(club.dues.count(), 2)

    def test_running_twice_does_not_bill_twice(self):
        # Idempotent by construction: once renewed, the club's latest period ends a year out,
        # which is past the horizon.
        club = self.ending_in(20)

        call_command("renew_subscriptions", stdout=StringIO())
        call_command("renew_subscriptions", stdout=StringIO())

        self.assertEqual(club.dues.count(), 2)

    def test_a_club_that_opted_out_is_not_renewed(self):
        club = self.ending_in(20, auto_renew=False)

        self.assertNotIn(club, [s.club for s in subscriptions_due_for_renewal()])

    def test_an_archived_club_is_not_renewed(self):
        # Reactivation is the way back, and it opens a period of its own.
        club = self.ending_in(20)
        club.archive()

        self.assertNotIn(club, [s.club for s in subscriptions_due_for_renewal()])

    def test_the_new_period_is_billed_at_the_price_in_force_then(self):
        club = self.ending_in(20)
        PlanPrice.objects.create(plan=self.plan, active_from=self.today, amount=Decimal("900.00"))

        due = renew(club.subscription)

        self.assertEqual(due.amount, Decimal("900.00"))  # the new rate
        self.assertEqual(club.dues.order_by("period_start").first().amount, Decimal("500.00"))  # the old one, untouched

    def test_the_new_period_is_invoiced(self):
        club = self.ending_in(20)

        due = renew(club.subscription)

        self.assertTrue(due.invoice.number.startswith("INV-"))

    def test_a_dry_run_issues_nothing(self):
        club = self.ending_in(20)
        out = StringIO()

        call_command("renew_subscriptions", "--dry-run", stdout=out)

        self.assertEqual(club.dues.count(), 1)
        self.assertIn("would renew", out.getvalue())

    def test_the_command_issues_by_default(self):
        # The opposite asymmetry to archiving: NOT acting is the expensive failure here,
        # because a club that is never billed is never chased either.
        club = self.ending_in(20)

        call_command("renew_subscriptions", stdout=StringIO())

        self.assertEqual(club.dues.count(), 2)

    def test_an_unpriced_plan_fails_loudly_without_stopping_the_others(self):
        priced = self.ending_in(20)
        broken = Club.objects.create(name="Unpriced FC")
        subscribe(broken, self.plan, start=self.today - datetime.timedelta(days=350))
        # Its next period starts beyond the last price... by removing every price, it cannot bill.
        PlanPrice.objects.all().delete()
        cheap = Plan.objects.create(name="Cheap")
        PlanPrice.objects.create(plan=cheap, active_from=self.today - datetime.timedelta(days=1200), amount=Decimal("100.00"))
        priced.subscription.plan = cheap
        priced.subscription.save()

        with self.assertRaises(CommandError):
            call_command("renew_subscriptions", stdout=StringIO(), stderr=StringIO())

        # ...and the club that COULD be billed still was.
        self.assertEqual(priced.dues.count(), 2)

    def test_a_subscription_with_no_period_at_all_is_renewed(self):
        club = Club.objects.create(name="Orphan FC")
        Subscription.objects.create(club=club, plan=self.plan)

        self.assertIn(club, [s.club for s in subscriptions_due_for_renewal()])

    def test_it_says_so_when_there_is_nothing_to_renew(self):
        self.assertIn("Nothing to renew", self.run_renewal())

    def run_renewal(self, *args):
        out = StringIO()
        call_command("renew_subscriptions", *args, stdout=out)
        return out.getvalue()


class RenewedButUnpaidTests(BillingTestBase):
    """A club auto-renewed that never pays the new fee flows through the ordinary
    unpaid -> grace -> overdue -> archive path. Renewal creates a normal Due; it does not
    create a special case, and the safety net that the never-billed club slipped past now
    fires, because there IS an unpaid due."""

    def lapsed_club(self):
        """A club on its first, PAID period — far enough back that a renewal from its end is
        itself already past grace, so only the renewal's payment state decides the outcome."""
        club = Club.objects.create(name="Renewed FC")
        subscribe(club, self.plan, start=self.today - datetime.timedelta(days=800))
        first = club.dues.first()
        record_payment(first, first.amount)  # the FIRST period is settled; only the renewal is in question
        return club

    def test_an_unpaid_renewal_becomes_overdue_and_archivable(self):
        club = self.lapsed_club()
        renewed = renew(club.subscription)  # continues from the first period's end, unpaid

        self.assertTrue(renewed.is_overdue(self.today))
        self.assertIn(renewed, dues_overdue(self.today))
        self.assertIn(club, [d.club for d in archivable_clubs(self.today)])

    def test_a_paid_renewal_is_not_chased(self):
        club = self.lapsed_club()
        renewed = renew(club.subscription)
        record_payment(renewed, renewed.amount)

        self.assertNotIn(club, [d.club for d in archivable_clubs(self.today)])


class TrialTests(BillingTestBase):
    """A club with no subscription yet can be started on a short trial that switches
    itself to a pre-selected plan automatically once the trial period is renewed --
    see billing.services.dues.start_trial and the trial-conversion check in
    open_period()."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # A trial is a plan whose own duration_months IS the trial length -- there is no
        # trial_months argument any more.
        cls.trial_plan = Plan.objects.create(name="Trial", duration_months=2, is_trial=True, grace_days=14, renewal_lead_days=7)
        PlanPrice.objects.create(plan=cls.trial_plan, active_from=cls.today - datetime.timedelta(days=1200), amount=Decimal("50.00"))

    def test_start_trial_creates_a_short_trial_period(self):
        due = start_trial(self.club, self.trial_plan, post_trial_plan=self.plan)

        subscription = self.club.subscription
        self.assertEqual(subscription.plan, self.trial_plan)
        self.assertEqual(subscription.post_trial_plan, self.plan)
        self.assertEqual(subscription.trial_ends_at, due.period_end)
        self.assertTrue(due.is_trial)
        # Roughly 2 months, nowhere near the standard ~1-year period.
        self.assertLess((due.period_end - due.period_start).days, 65)

    def test_start_trial_refuses_if_already_subscribed(self):
        subscribe(self.club, self.plan)

        with self.assertRaises(BillingError):
            start_trial(self.club, self.trial_plan, post_trial_plan=self.plan)

    def test_the_trials_length_comes_from_its_plan(self):
        due = start_trial(self.club, self.trial_plan, post_trial_plan=self.plan)

        self.assertEqual(due.period_end, add_months(due.period_start, 2) - datetime.timedelta(days=1))

    def test_renewing_after_the_trial_switches_to_the_post_trial_plan(self):
        start_trial(self.club, self.trial_plan, post_trial_plan=self.plan)

        due = renew(self.club.subscription)

        self.club.refresh_from_db()
        self.assertEqual(self.club.subscription.plan, self.plan)
        self.assertIsNone(self.club.subscription.trial_ends_at)
        self.assertIsNone(self.club.subscription.post_trial_plan)
        self.assertEqual(due.plan, self.plan)
        self.assertFalse(due.is_trial)
        self.assertEqual(due.amount, Decimal("500.00"))

    def test_manually_opening_the_next_period_also_switches_plan(self):
        # Same conversion must fire via the control panel's "Open period" button, which
        # calls open_period() directly rather than renew().
        start_trial(self.club, self.trial_plan, post_trial_plan=self.plan)

        open_period(self.club)

        self.club.refresh_from_db()
        self.assertEqual(self.club.subscription.plan, self.plan)

    def test_a_trial_nearing_its_end_is_picked_up_for_renewal(self):
        # Inside the TRIAL PLAN's own 7-day lead, not the 30-day one an annual plan uses:
        # a 2-month trial renewed a month early would be renewed before it had begun.
        start_trial(self.club, self.trial_plan, post_trial_plan=self.plan, start=self.today - datetime.timedelta(days=57))

        self.assertIn(self.club, [s.club for s in subscriptions_due_for_renewal()])

    def test_a_trial_outside_its_own_lead_window_is_not_yet_renewed(self):
        # Same trial 7 days earlier in its life: an annual plan's 30-day lead would have
        # picked this up, and the per-plan lead is exactly what stops that.
        start_trial(self.club, self.trial_plan, post_trial_plan=self.plan, start=self.today - datetime.timedelta(days=40))

        self.assertNotIn(self.club, [s.club for s in subscriptions_due_for_renewal()])

    def test_a_zero_amount_trial_is_created_already_paid(self):
        free_plan = Plan.objects.create(name="Free Trial")
        PlanPrice.objects.create(plan=free_plan, active_from=self.today - datetime.timedelta(days=1200), amount=Decimal("0.00"))

        due = start_trial(self.club, free_plan, post_trial_plan=self.plan)

        self.assertEqual(due.status, Due.Status.PAID)
        self.assertIsNotNone(due.paid_at)
        far_future = due.grace_until + datetime.timedelta(days=100)
        self.assertNotIn(due, dues_overdue(far_future))
        self.assertNotIn(self.club, [d.club for d in archivable_clubs(far_future)])


class PlanClockTests(BillingTestBase):
    """The three per-plan clocks, and the constraints that keep them sane -- see BILLING.md §3."""

    def make_plan(self, **kwargs):
        # A short plan cannot keep the annual defaults -- 30 days' lead on a 1-month period is
        # exactly what the constraints forbid, so scale them down with the duration.
        months = kwargs.get("duration_months", DEFAULT_DURATION_MONTHS)
        defaults = {"name": f"Plan {Plan.objects.count()}", "renewal_lead_days": min(DEFAULT_RENEWAL_LEAD_DAYS, months * 7), "grace_days": min(DEFAULT_GRACE_DAYS, months * 14)}
        plan = Plan.objects.create(**defaults | kwargs)
        PlanPrice.objects.create(plan=plan, active_from=self.today - datetime.timedelta(days=1200), amount=Decimal("10.00"))
        return plan

    def test_a_monthly_plan_gets_a_one_month_period(self):
        plan = self.make_plan(duration_months=1)

        due = open_period(self.club, plan=plan, start=datetime.date(2026, 3, 1))

        self.assertEqual(due.period_end, datetime.date(2026, 3, 31))

    def test_a_quarterly_plan_gets_a_three_month_period(self):
        plan = self.make_plan(duration_months=3)

        due = open_period(self.club, plan=plan, start=datetime.date(2026, 3, 1))

        self.assertEqual(due.period_end, datetime.date(2026, 5, 31))

    def test_grace_days_are_per_plan(self):
        plan = self.make_plan(duration_months=1, grace_days=14)

        due = open_period(self.club, plan=plan, start=datetime.date(2026, 3, 1))

        self.assertEqual(due.grace_until, datetime.date(2026, 3, 15))

    def test_editing_a_plans_grace_does_not_move_an_open_period(self):
        # grace_until is a stored snapshot for the same reason `amount` is: repricing the
        # plan must not silently re-date an archiving already in flight.
        plan = self.make_plan(grace_days=30)
        due = open_period(self.club, plan=plan, start=self.today)
        original = due.grace_until

        plan.grace_days = 1
        plan.save(update_fields=["grace_days"])
        due.refresh_from_db()

        self.assertEqual(due.grace_until, original)

    def test_a_lead_longer_than_the_period_is_rejected(self):
        with self.assertRaises(IntegrityError):
            Plan.objects.create(name="Runaway", duration_months=1, renewal_lead_days=90)

    def test_grace_longer_than_the_period_is_rejected(self):
        with self.assertRaises(IntegrityError):
            Plan.objects.create(name="Never archives", duration_months=1, grace_days=90)

    def test_full_clean_reports_an_impossible_lead_as_a_form_error(self):
        # Not an IntegrityError/500: a platform admin typing this into the plan form should
        # be told which field is wrong.
        plan = Plan(name="Runaway", duration_months=1, renewal_lead_days=90, grace_days=14)

        with self.assertRaises(ValidationError) as caught:
            plan.full_clean()

        self.assertIn("renewal_lead_days", caught.exception.error_dict)

    def test_renewal_lead_is_read_from_each_plan(self):
        monthly = self.make_plan(duration_months=1, renewal_lead_days=7, grace_days=14)
        club = Club.objects.create(name="Monthly FC")
        # Period ends in 3 days: inside a 7-day lead, well outside an annual plan's 30.
        subscribe(club, monthly, start=self.today - datetime.timedelta(days=27))

        self.assertIn(club, [s.club for s in subscriptions_due_for_renewal()])

    def test_an_explicit_lead_days_overrides_every_plan(self):
        monthly = self.make_plan(duration_months=1, renewal_lead_days=1, grace_days=14)
        club = Club.objects.create(name="Override FC")
        subscribe(club, monthly, start=self.today - datetime.timedelta(days=20))

        self.assertNotIn(club, [s.club for s in subscriptions_due_for_renewal()])
        self.assertIn(club, [s.club for s in subscriptions_due_for_renewal(lead_days=30)])


class BillingNoticeTests(BillingTestBase):
    """What a club's own admins are told -- see billing/services/notices.py."""

    def test_no_notice_when_nothing_is_owed(self):
        due = self.bill()
        record_payment(due, Decimal("500.00"))

        self.assertIsNone(club_billing_notice(self.club, self.today))

    def test_no_notice_for_a_club_that_was_never_billed(self):
        self.assertIsNone(club_billing_notice(self.club, self.today))

    def test_a_period_issued_ahead_of_its_start_is_only_informational(self):
        self.bill(start=self.today + datetime.timedelta(days=10))

        self.assertEqual(club_billing_notice(self.club, self.today).level, "info")

    def test_an_unpaid_started_period_warns(self):
        subscribe(self.club, self.plan, start=self.today - datetime.timedelta(days=1))

        notice = club_billing_notice(self.club, self.today)

        self.assertEqual(notice.level, "warning")
        self.assertEqual(notice.amount_outstanding, Decimal("500.00"))
        self.assertFalse(notice.is_urgent)

    def test_the_last_week_before_archiving_is_urgent(self):
        subscribe(self.club, self.plan, start=self.today - datetime.timedelta(days=DEFAULT_GRACE_DAYS - 2))

        notice = club_billing_notice(self.club, self.today)

        self.assertEqual(notice.level, "error")
        self.assertTrue(notice.is_urgent)
        self.assertEqual(notice.days_until_archive, 2)

    def test_an_overdue_period_is_urgent_with_a_negative_countdown(self):
        subscribe(self.club, self.plan, start=self.today - datetime.timedelta(days=DEFAULT_GRACE_DAYS + 5))

        notice = club_billing_notice(self.club, self.today)

        self.assertEqual(notice.level, "error")
        self.assertLess(notice.days_until_archive, 0)

    def test_auto_archive_off_still_reports_the_debt_but_promises_no_archiving(self):
        subscribe(self.club, self.plan, start=self.today - datetime.timedelta(days=1), auto_archive=False)

        notice = club_billing_notice(self.club, self.today)

        self.assertEqual(notice.amount_outstanding, Decimal("500.00"))
        self.assertFalse(notice.will_archive)

    def test_the_soonest_archiving_due_is_the_one_reported(self):
        self.bill(start=self.today - datetime.timedelta(days=1))
        later = self.bill(start=self.today + datetime.timedelta(days=400))

        self.assertNotEqual(club_billing_notice(self.club, self.today).due, later)


class BillingReminderTests(BillingTestBase):
    """Reminder emails -- see billing/services/reminders.py. Sent once per escalation
    level, because the command is on a daily cron."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        user = User.objects.create_user(email="admin@ajax.example", password="pw-secret-123")
        member = Member.objects.create(user=user, first_name="Ada", last_name="Admin")
        ClubRole.objects.create(club=cls.club, member=member, role=ClubRole.Roles.ADMIN)
        subscribe(cls.club, cls.plan, start=cls.today - datetime.timedelta(days=1))
        cls.due = cls.club.dues.first()

    def test_a_reminder_goes_to_the_club_admins(self):
        self.assertEqual(admin_emails(self.club), ["admin@ajax.example"])

    def make_overdue_club(self, name, *, auto_archive):
        # A separate club rather than resubscribing self.club: open_period() caches
        # club.subscription on the instance it's given, so calling subscribe() twice
        # against the SAME Python object -- only possible by reusing one across two calls,
        # never a real request -- would read the first call's now-stale cached subscription.
        club = Club.objects.create(name=name)
        user = User.objects.create_user(email=f"{name.lower()}@ajax.example", password="pw-secret-123")
        member = Member.objects.create(user=user, first_name="A", last_name="Admin")
        ClubRole.objects.create(club=club, member=member, role=ClubRole.Roles.ADMIN)
        subscribe(club, self.plan, start=self.today - datetime.timedelta(days=DEFAULT_GRACE_DAYS + 5), auto_archive=auto_archive)
        return club

    def test_an_overdue_reminder_threatens_archiving_when_it_will_happen(self):
        club = self.make_overdue_club("Archive On", auto_archive=True)

        notice = club_billing_notice(club, self.today)
        self.assertTrue(notice.will_archive)
        send_reminder(club, notice, recipients=["archive-on@ajax.example"])

        self.assertIn("about to be archived", mail.outbox[0].subject)
        self.assertIn("archived", mail.outbox[0].body)

    def test_an_overdue_reminder_does_not_threaten_archiving_when_auto_archive_is_off(self):
        # This is the bug the two-column-modal review turned up: the subject branched only
        # on notice.level, so a club that will NEVER be archived still got told it was
        # "about to be archived" -- while the body correctly said otherwise.
        club = self.make_overdue_club("Archive Off", auto_archive=False)

        notice = club_billing_notice(club, self.today)
        self.assertFalse(notice.will_archive)
        send_reminder(club, notice, recipients=["archive-off@ajax.example"])

        self.assertNotIn("about to be archived", mail.outbox[0].subject)
        self.assertNotIn("archived", mail.outbox[0].body)
        self.assertIn("good standing", mail.outbox[0].body)

    def test_sending_records_the_level_and_fills_the_outbox(self):
        notice = club_billing_notice(self.club, self.today)
        send_reminder(self.club, notice, recipients=["admin@ajax.example"])

        self.due.refresh_from_db()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.due.last_reminder_level, notice.level)
        self.assertIsNotNone(self.due.last_reminder_sent_at)

    def test_a_second_run_at_the_same_level_sends_nothing(self):
        notice = club_billing_notice(self.club, self.today)
        send_reminder(self.club, notice, recipients=["admin@ajax.example"])
        self.due.refresh_from_db()

        results = reminders_to_send([self.club], self.today)

        self.assertFalse(results[0].sent)
        self.assertIn("already reminded", results[0].skipped_reason)

    def test_an_escalation_gets_through(self):
        send_reminder(self.club, club_billing_notice(self.club, self.today), recipients=["admin@ajax.example"])

        # Far enough on that the same due is now urgent rather than merely a warning.
        later = self.today + datetime.timedelta(days=DEFAULT_GRACE_DAYS)
        results = reminders_to_send([self.club], later)

        self.assertTrue(results[0].sent)
        self.assertEqual(results[0].notice.level, "error")

    def test_force_resends_at_the_same_level(self):
        send_reminder(self.club, club_billing_notice(self.club, self.today), recipients=["admin@ajax.example"])
        self.due.refresh_from_db()

        self.assertTrue(reminders_to_send([self.club], self.today, force=True)[0].sent)

    def test_a_club_with_no_reachable_admin_is_reported_not_skipped_silently(self):
        ClubRole.objects.all().delete()

        results = reminders_to_send([self.club], self.today)

        self.assertFalse(results[0].sent)
        self.assertIn("no club admin", results[0].skipped_reason)

    def test_a_settled_club_produces_no_reminder(self):
        record_payment(self.due, Decimal("500.00"))

        self.assertEqual(reminders_to_send([self.club], self.today), [])


class PlanVisibilityTests(BillingTestBase):
    """Plan.objects.visible() -- see PlanQuerySet."""

    def test_a_plain_plan_is_visible(self):
        self.assertIn(self.plan, Plan.objects.visible())

    def test_a_soft_deleted_plan_is_excluded(self):
        self.plan.deleted_at = timezone.now()
        self.plan.save(update_fields=["deleted_at"])

        self.assertNotIn(self.plan, Plan.objects.visible())

    def test_the_default_manager_still_returns_a_soft_deleted_plan(self):
        # Django admin, and anything reading historical data, must still be able to find it.
        self.plan.deleted_at = timezone.now()
        self.plan.save(update_fields=["deleted_at"])

        self.assertIn(self.plan, Plan.objects.all())


class PlanDeletionTests(BillingTestBase):
    """billing.services.plans -- see its module docstring for the full reasoning."""

    def test_a_never_billed_plan_is_hard_deleted(self):
        unused = Plan.objects.create(name="Unused")

        impact = delete_plan(unused)

        self.assertTrue(impact.will_hard_delete)
        self.assertFalse(Plan.objects.filter(pk=unused.pk).exists())

    def test_a_plan_with_a_due_cannot_be_hard_deleted(self):
        self.bill()

        impact = delete_plan(self.plan)

        self.assertFalse(impact.will_hard_delete)
        self.assertTrue(Plan.objects.filter(pk=self.plan.pk).exists())

    def test_a_cancelled_due_still_protects_the_plan(self):
        # PROTECT does not care about the referencing row's own status -- a cancelled due is
        # still a row, and financial history includes rows nobody expects to see again.
        due = self.bill()
        due.status = Due.Status.CANCELLED
        due.save(update_fields=["status"])

        impact = delete_plan(self.plan)

        self.assertFalse(impact.will_hard_delete)

    def test_soft_delete_marks_the_plan_inactive_and_deleted(self):
        subscribe(self.club, self.plan)

        delete_plan(self.plan)
        self.plan.refresh_from_db()

        self.assertTrue(self.plan.is_deleted)
        self.assertFalse(self.plan.is_active)

    def test_deleting_unsubscribes_the_club_entirely_rather_than_nulling_a_field(self):
        subscribe(self.club, self.plan)

        delete_plan(self.plan)

        self.assertFalse(Subscription.objects.filter(club=self.club).exists())

    def test_a_deleted_plans_historical_due_is_untouched(self):
        subscribe(self.club, self.plan)
        due = self.club.dues.first()
        amount, period_end, grace_until = due.amount, due.period_end, due.grace_until

        delete_plan(self.plan)
        due.refresh_from_db()

        self.assertEqual(due.plan_id, self.plan.pk)
        self.assertEqual(due.amount, amount)
        self.assertEqual(due.period_end, period_end)
        self.assertEqual(due.grace_until, grace_until)

    def test_a_club_not_on_the_plan_is_unaffected(self):
        other_plan = Plan.objects.create(name="Other")
        PlanPrice.objects.create(plan=other_plan, active_from=self.today - datetime.timedelta(days=1200), amount=Decimal("100.00"))
        untouched = Club.objects.create(name="Untouched FC")
        subscribe(untouched, other_plan)

        delete_plan(self.plan)

        self.assertTrue(Subscription.objects.filter(club=untouched, plan=other_plan).exists())

    def test_a_trial_scheduled_to_convert_to_the_deleted_plan_is_cleared(self):
        trial_plan = Plan.objects.create(name="Trial", is_trial=True, duration_months=2, renewal_lead_days=7, grace_days=14)
        PlanPrice.objects.create(plan=trial_plan, active_from=self.today - datetime.timedelta(days=1200), amount=Decimal("0.00"))
        club = Club.objects.create(name="Mid Trial FC")
        start_trial(club, trial_plan, post_trial_plan=self.plan)

        impact = delete_plan(self.plan)

        self.assertEqual([c.pk for c in impact.broken_trial_clubs], [club.pk])
        club.refresh_from_db()
        subscription = club.subscription
        self.assertEqual(subscription.plan, trial_plan)
        self.assertIsNone(subscription.trial_ends_at)
        self.assertIsNone(subscription.post_trial_plan)

    def test_a_club_currently_on_the_plan_is_not_also_counted_as_a_broken_trial(self):
        subscribe(self.club, self.plan)

        impact = delete_plan(self.plan)

        self.assertEqual(impact.unsubscribed_clubs, [self.club])
        self.assertEqual(impact.broken_trial_clubs, [])

    def test_plan_deletion_impact_is_read_only(self):
        subscribe(self.club, self.plan)

        plan_deletion_impact(self.plan)

        self.assertTrue(Subscription.objects.filter(club=self.club).exists())
        self.assertFalse(self.plan.is_deleted)
