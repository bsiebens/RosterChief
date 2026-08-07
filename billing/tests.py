import datetime
import sys
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from club.models import Club

from .models import GRACE_DAYS, Due, Invoice, Subscription, Tier, TierPrice, add_one_year
from .services import BillingError
from .services.dues import archivable_clubs, dues_in_grace, dues_overdue, next_period_start, open_period, reactivate, record_payment, remove_payment, renew, start_trial, subscribe, subscriptions_due_for_renewal, waive
from .services.invoices import invoice_pdf, issue_invoice, render_pdf


class BillingTestBase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.club = Club.objects.create(name="Ajax United")
        self.tier = Tier.objects.create(name="Standard")
        # Priced well back, so a backdated (lapsed) period still has a price in force —
        # opening one before any price existed is refused, and rightly so.
        TierPrice.objects.create(tier=self.tier, active_from=self.today - datetime.timedelta(days=1200), amount=Decimal("500.00"))

    def bill(self, start=None, club=None):
        return open_period(club or self.club, start=start, tier=self.tier)


class TierPriceTests(BillingTestBase):
    def test_the_price_in_force_is_the_latest_one_that_has_started(self):
        TierPrice.objects.create(tier=self.tier, active_from=self.today, amount=Decimal("600.00"))

        self.assertEqual(self.tier.price_on(self.today - datetime.timedelta(days=1)), Decimal("500.00"))
        self.assertEqual(self.tier.price_on(self.today), Decimal("600.00"))

    def test_a_future_price_does_not_apply_yet(self):
        TierPrice.objects.create(tier=self.tier, active_from=self.today + datetime.timedelta(days=30), amount=Decimal("600.00"))

        self.assertEqual(self.tier.price_on(self.today), Decimal("500.00"))

    def test_a_tier_with_no_price_yet_cannot_be_billed(self):
        # None must never be read as free.
        empty = Tier.objects.create(name="Enterprise")

        self.assertIsNone(empty.price_on(self.today))

        with self.assertRaises(BillingError):
            open_period(self.club, tier=empty)


class PeriodTests(BillingTestBase):
    def test_a_period_runs_a_rolling_year_with_a_grace_tail(self):
        due = self.bill(start=datetime.date(2026, 3, 1))

        self.assertEqual(due.period_end, datetime.date(2027, 2, 28))
        self.assertEqual(due.grace_until, due.period_end + datetime.timedelta(days=GRACE_DAYS))

    def test_a_leap_day_period_does_not_explode(self):
        # 29 February has no counterpart in a common year.
        self.assertEqual(add_one_year(datetime.date(2028, 2, 29)), datetime.date(2029, 2, 28))

    def test_the_next_period_continues_from_the_last_one(self):
        # Not from today: a club that pays two months late has still used those two months,
        # and restarting the clock at the payment date would quietly gift them away.
        first = self.bill(start=self.today - datetime.timedelta(days=400))

        self.assertEqual(next_period_start(self.club), first.period_end + datetime.timedelta(days=1))

    def test_a_first_period_starts_today(self):
        self.assertEqual(next_period_start(self.club), self.today)

    def test_the_amount_is_snapshotted_at_the_price_of_the_day(self):
        due = self.bill()
        TierPrice.objects.create(tier=self.tier, active_from=self.today + datetime.timedelta(days=1), amount=Decimal("900.00"))
        due.refresh_from_db()

        # Raising the rate must not rewrite what was already billed.
        self.assertEqual(due.amount, Decimal("500.00"))

    def test_a_club_cannot_be_billed_twice_for_one_period(self):
        self.bill(start=self.today)

        with self.assertRaises(BillingError):
            self.bill(start=self.today)

    def test_a_club_with_no_tier_cannot_be_billed(self):
        with self.assertRaises(BillingError):
            open_period(Club.objects.create(name="Feyenoord"))

    def test_subscribing_puts_a_club_on_a_tier_and_opens_a_period(self):
        club = Club.objects.create(name="Feyenoord")

        subscribe(club, self.tier)

        self.assertEqual(Subscription.objects.get(club=club).tier, self.tier)
        self.assertEqual(club.dues.count(), 1)


class PaymentTests(BillingTestBase):
    def setUp(self):
        super().setUp()
        self.due = self.bill()

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
    LAPSED = 365 + GRACE_DAYS + 10

    def test_a_period_past_its_end_but_inside_grace_is_in_grace(self):
        due = self.bill(start=self.today - datetime.timedelta(days=370))

        self.assertTrue(due.is_in_grace(self.today))
        self.assertFalse(due.is_overdue(self.today))
        self.assertIn(due, dues_in_grace(self.today))

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
        subscribe(self.club, self.tier, start=self.today - datetime.timedelta(days=self.LAPSED))

        self.assertEqual(archivable_clubs(self.today).count(), 1)

    def test_a_club_that_opted_out_is_never_archived(self):
        # auto_archive off is how you stop a club you are negotiating with from being
        # switched off overnight.
        subscribe(self.club, self.tier, start=self.today - datetime.timedelta(days=self.LAPSED), auto_archive=False)

        self.assertEqual(archivable_clubs(self.today).count(), 0)

    def test_an_already_archived_club_is_not_archived_again(self):
        subscribe(self.club, self.tier, start=self.today - datetime.timedelta(days=self.LAPSED))
        self.club.archive()

        self.assertEqual(archivable_clubs(self.today).count(), 0)


class ArchiveCommandTests(BillingTestBase):
    def setUp(self):
        super().setUp()
        subscribe(self.club, self.tier, start=self.today - datetime.timedelta(days=365 + GRACE_DAYS + 10))

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
    def setUp(self):
        super().setUp()
        # Through subscribe(), not open_period(): reactivating reads the club's tier off its
        # subscription, and a club billed without one cannot be re-billed later.
        subscribe(self.club, self.tier, start=self.today - datetime.timedelta(days=400))
        self.first = self.club.dues.first()
        self.club.archive()

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
        second = open_period(Club.objects.create(name="Feyenoord"), tier=self.tier).invoice

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

        self.assertEqual(str(self.tier), "Standard")
        self.assertIn("500.00", str(self.tier.prices.first()))
        self.assertIn("Ajax United", str(due))
        self.assertIn("10.00", str(payment))
        self.assertIn("INV-", str(due.invoice))
        self.assertIn("Standard", str(subscribe(Club.objects.create(name="PSV"), self.tier)))


class RenewalTests(BillingTestBase):
    """The leak this closes: a club whose period lapses with its last due PAID owes nothing,
    so dues_overdue() is empty, so archive_overdue_clubs never fires — and the club keeps
    using the platform for free while every number on the dashboard stays green."""

    def ending_in(self, days, **kwargs):
        """A club whose current period ends `days` from now."""
        club = Club.objects.create(name=f"Club {days}")
        subscribe(club, self.tier, start=self.today - datetime.timedelta(days=365 - days), **kwargs)
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
        TierPrice.objects.create(tier=self.tier, active_from=self.today, amount=Decimal("900.00"))

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

    def test_an_unpriced_tier_fails_loudly_without_stopping_the_others(self):
        priced = self.ending_in(20)
        broken = Club.objects.create(name="Unpriced FC")
        subscribe(broken, self.tier, start=self.today - datetime.timedelta(days=350))
        # Its next period starts beyond the last price... by removing every price, it cannot bill.
        TierPrice.objects.all().delete()
        cheap = Tier.objects.create(name="Cheap")
        TierPrice.objects.create(tier=cheap, active_from=self.today - datetime.timedelta(days=1200), amount=Decimal("100.00"))
        priced.subscription.tier = cheap
        priced.subscription.save()

        with self.assertRaises(CommandError):
            call_command("renew_subscriptions", stdout=StringIO(), stderr=StringIO())

        # ...and the club that COULD be billed still was.
        self.assertEqual(priced.dues.count(), 2)

    def test_a_subscription_with_no_period_at_all_is_renewed(self):
        club = Club.objects.create(name="Orphan FC")
        Subscription.objects.create(club=club, tier=self.tier)

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
        subscribe(club, self.tier, start=self.today - datetime.timedelta(days=800))
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

    def setUp(self):
        super().setUp()
        self.trial_tier = Tier.objects.create(name="Trial")
        TierPrice.objects.create(tier=self.trial_tier, active_from=self.today - datetime.timedelta(days=1200), amount=Decimal("50.00"))

    def test_start_trial_creates_a_short_trial_period(self):
        due = start_trial(self.club, self.trial_tier, post_trial_tier=self.tier, trial_months=2)

        subscription = self.club.subscription
        self.assertEqual(subscription.tier, self.trial_tier)
        self.assertEqual(subscription.post_trial_tier, self.tier)
        self.assertEqual(subscription.trial_ends_at, due.period_end)
        self.assertTrue(due.is_trial)
        # Roughly 2 months, nowhere near the standard ~1-year period.
        self.assertLess((due.period_end - due.period_start).days, 65)

    def test_start_trial_refuses_if_already_subscribed(self):
        subscribe(self.club, self.tier)

        with self.assertRaises(BillingError):
            start_trial(self.club, self.trial_tier, post_trial_tier=self.tier, trial_months=2)

    def test_start_trial_refuses_a_non_positive_length(self):
        with self.assertRaises(BillingError):
            start_trial(self.club, self.trial_tier, post_trial_tier=self.tier, trial_months=0)

    def test_renewing_after_the_trial_switches_to_the_post_trial_tier(self):
        start_trial(self.club, self.trial_tier, post_trial_tier=self.tier, trial_months=2)

        due = renew(self.club.subscription)

        self.club.refresh_from_db()
        self.assertEqual(self.club.subscription.tier, self.tier)
        self.assertIsNone(self.club.subscription.trial_ends_at)
        self.assertIsNone(self.club.subscription.post_trial_tier)
        self.assertEqual(due.tier, self.tier)
        self.assertFalse(due.is_trial)
        self.assertEqual(due.amount, Decimal("500.00"))

    def test_manually_opening_the_next_period_also_switches_tier(self):
        # Same conversion must fire via the control panel's "Open period" button, which
        # calls open_period() directly rather than renew().
        start_trial(self.club, self.trial_tier, post_trial_tier=self.tier, trial_months=2)

        open_period(self.club)

        self.club.refresh_from_db()
        self.assertEqual(self.club.subscription.tier, self.tier)

    def test_a_trial_nearing_its_end_is_picked_up_for_renewal(self):
        start_trial(self.club, self.trial_tier, post_trial_tier=self.tier, trial_months=2, start=self.today - datetime.timedelta(days=50))

        self.assertIn(self.club, [s.club for s in subscriptions_due_for_renewal()])

    def test_a_zero_amount_trial_is_created_already_paid(self):
        free_tier = Tier.objects.create(name="Free Trial")
        TierPrice.objects.create(tier=free_tier, active_from=self.today - datetime.timedelta(days=1200), amount=Decimal("0.00"))

        due = start_trial(self.club, free_tier, post_trial_tier=self.tier, trial_months=2)

        self.assertEqual(due.status, Due.Status.PAID)
        self.assertIsNotNone(due.paid_at)
        far_future = due.grace_until + datetime.timedelta(days=100)
        self.assertNotIn(due, dues_overdue(far_future))
        self.assertNotIn(self.club, [d.club for d in archivable_clubs(far_future)])
