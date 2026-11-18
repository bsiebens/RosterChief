"""Billing cycles: a plan can be billed monthly, quarterly, yearly ... each with its own prices, and plans can be put in the order the platform wants."""

import datetime

from django.core.exceptions import ValidationError

from features import services as feature_services
from features.models import Plan, Subscription

from .models import ClubBilling, Due, PlanCycle, PlanPrice
from .services import BillingError
from .services import dues as dues_svc
from .services import plan_change as pc
from .tests import SWITCH, ApiBase, BillingTestBase, M, PlanChangeBase, at, make_club

D = datetime.date
START = D(2026, 1, 1)


def add_cycle(plan, months, *, price, lead=5, grace=7, position=None, **extra):
    cycle = PlanCycle.objects.create(plan=plan, duration_months=months, renewal_lead_days=lead, grace_days=grace, position=position if position is not None else plan.cycles.count(), **extra)
    PlanPrice.objects.create(plan=plan, cycle=cycle, active_from=D(2000, 1, 1), amount=M(price))
    return cycle


class CycleModelTests(BillingTestBase):
    def test_a_new_plan_gets_a_yearly_cycle_and_prices_default_to_it(self):
        plan = Plan.objects.create(code="fresh", name="Fresh")
        cycle = plan.cycles.get()
        self.assertEqual((cycle.duration_months, cycle.display_name), (12, "Yearly"))
        price = PlanPrice.objects.create(plan=plan, active_from=D(2026, 1, 1), amount=M("10.00"))
        self.assertEqual(price.cycle, cycle)

    def test_names_follow_the_length_unless_a_label_is_given(self):
        plan = Plan.objects.create(code="names", name="Names")
        self.assertEqual([PlanCycle(plan=plan, duration_months=n).display_name for n in (1, 3, 6, 12, 5)], ["Monthly", "Quarterly", "Half-yearly", "Yearly", "Every 5 months"])
        self.assertEqual(PlanCycle(plan=plan, duration_months=1, label="Pay as you go").display_name, "Pay as you go")

    def test_a_plan_cannot_have_two_cycles_of_the_same_length(self):
        with self.assertRaises(Exception):  # noqa: B017  (IntegrityError)
            PlanCycle.objects.create(plan=self.plan, duration_months=12)

    def test_lead_and_grace_must_fit_in_the_period(self):
        with self.assertRaises(ValidationError):
            PlanCycle(plan=self.plan, duration_months=1, renewal_lead_days=40).full_clean(exclude=["plan"])
        with self.assertRaises(ValidationError):
            PlanCycle(plan=self.plan, duration_months=1, grace_days=40).full_clean(exclude=["plan"])


class CycleBillingTests(BillingTestBase):
    def setUp(self):
        super().setUp()
        self.yearly = self.plan.cycles.get()
        self.monthly = add_cycle(self.plan, 1, price="12.50")

    def test_subscribing_on_a_cycle_opens_a_period_of_that_length_and_price(self):
        club = make_club("Monthly Club", billed=False)
        with at(START):
            billing, due = dues_svc.subscribe(club, self.plan, start=START, cycle=self.monthly)
        self.assertEqual((due.period_start, due.period_end, due.amount, due.cycle, due.grace_until), (START, D(2026, 1, 31), M("12.50"), self.monthly, D(2026, 1, 8)))
        self.assertEqual(billing.cycle, self.monthly)

    def test_without_a_choice_the_first_active_cycle_is_used_and_reordering_changes_it(self):
        first = make_club("First", billed=False)
        self.assertEqual(dues_svc.subscribe(first, self.plan, start=START)[1].cycle, self.yearly)
        PlanCycle.objects.filter(pk=self.monthly.pk).update(position=0)
        PlanCycle.objects.filter(pk=self.yearly.pk).update(position=1)
        second = make_club("Second", billed=False)
        self.assertEqual(dues_svc.subscribe(second, self.plan, start=START)[1].cycle, self.monthly)

    def test_an_inactive_cycle_cannot_be_chosen_but_a_club_on_it_keeps_renewing(self):
        club = make_club("Stays", billed=False)
        dues_svc.subscribe(club, self.plan, start=START, cycle=self.monthly)
        PlanCycle.objects.filter(pk=self.monthly.pk).update(is_active=False)
        self.monthly.refresh_from_db()
        with self.assertRaises(BillingError):
            dues_svc.subscribe(make_club("Late", billed=False), self.plan, start=START, cycle=self.monthly)
        renewed = dues_svc.open_period(club, plan=self.plan)
        self.assertEqual((renewed.cycle, renewed.period_start, renewed.period_end), (self.monthly, D(2026, 2, 1), D(2026, 2, 28)))

    def test_a_cycle_of_another_plan_is_refused(self):
        other = add_cycle(Plan.objects.create(code="other", name="Other"), 1, price="5.00")
        with self.assertRaises(BillingError):
            dues_svc.subscribe(make_club("Wrong", billed=False), self.plan, start=START, cycle=other)

    def test_prices_are_per_cycle(self):
        PlanPrice.objects.create(plan=self.plan, cycle=self.monthly, active_from=D(2026, 3, 1), amount=M("15.00"))
        self.assertEqual(dues_svc.price_on(self.plan, D(2026, 2, 1), self.monthly).amount, M("12.50"))
        self.assertEqual(dues_svc.price_on(self.plan, D(2026, 3, 1), self.monthly).amount, M("15.00"))
        self.assertEqual(dues_svc.price_on(self.plan, D(2026, 3, 1), self.yearly).amount, M("100.00"))
        self.assertEqual(dues_svc.price_on(self.plan, D(2026, 3, 1)).amount, M("100.00"))  # no cycle named: the plan's first one

    def test_a_cycle_without_a_price_cannot_be_billed(self):
        unpriced = PlanCycle.objects.create(plan=self.plan, duration_months=3, position=2)
        with self.assertRaises(BillingError):
            dues_svc.subscribe(make_club("Unpriced", billed=False), self.plan, start=START, cycle=unpriced)

    def test_the_invoice_lead_time_is_the_clubs_cycle(self):
        club = make_club("Lead", billed=False)
        billing, _ = dues_svc.subscribe(club, self.plan, start=START, cycle=self.monthly)  # period ends Jan 31, invoiced 5 days ahead
        self.assertNotIn(billing, dues_svc.subscriptions_due_for_renewal(D(2026, 1, 25)))
        self.assertIn(billing, dues_svc.subscriptions_due_for_renewal(D(2026, 1, 26)))
        yearly_club = make_club("Yearly Lead", billed=False)
        yearly_billing, _ = dues_svc.subscribe(yearly_club, self.plan, start=START, cycle=self.yearly)  # ends Dec 31, 30 days ahead
        self.assertNotIn(yearly_billing, dues_svc.subscriptions_due_for_renewal(D(2026, 1, 26)))
        self.assertIn(yearly_billing, dues_svc.subscriptions_due_for_renewal(D(2026, 12, 1)))

    def test_existing_periods_keep_their_cycle_and_dates(self):
        club = make_club("Frozen", billed=False)
        with at(START):
            _, due = dues_svc.subscribe(club, self.plan, start=START, cycle=self.monthly)
        PlanCycle.objects.filter(pk=self.monthly.pk).update(duration_months=2, grace_days=20)
        due.refresh_from_db()
        self.assertEqual((due.period_end, due.grace_until), (D(2026, 1, 31), D(2026, 1, 8)))


class CyclePlanChangeTests(PlanChangeBase):
    def setUp(self):
        super().setUp()
        self.yearly = self.plan.cycles.get()
        self.monthly = add_cycle(self.plan, 1, price="20.00")

    def test_switching_to_another_cycle_of_the_same_plan_prorates_and_starts_a_new_period(self):
        club, due = self.billed(paid=M("121.00"))
        subscriptions = Subscription.objects.filter(club=club).count()
        with at(SWITCH):
            change = pc.build_plan_change(club, self.plan, cycle=self.monthly)
        self.assertEqual((change.plan_from, change.plan_to, change.cycle_from, change.cycle_to), (self.plan, self.plan, self.yearly, self.monthly))
        with at(SWITCH):
            result = pc.execute_plan_change(club, self.plan, cycle=self.monthly)
        new = result.new_due
        self.assertEqual((new.cycle, new.period_start, new.period_end, new.amount), (self.monthly, SWITCH, D(2026, 8, 1), M("20.00")))
        due.refresh_from_db()
        self.assertTrue(due.is_prorated)
        self.assertEqual(ClubBilling.objects.get(club=club).cycle, self.monthly)
        self.assertEqual(Subscription.objects.filter(club=club).count(), subscriptions)  # the plan did not change, so its features are left alone

    def test_the_same_plan_and_cycle_is_refused(self):
        club, _ = self.billed()
        with at(SWITCH), self.assertRaises(BillingError):
            pc.build_plan_change(club, self.plan)
        with at(SWITCH), self.assertRaises(BillingError):
            pc.build_plan_change(club, self.plan, cycle=self.yearly)

    def test_changing_plan_without_a_cycle_uses_the_new_plans_first_cycle(self):
        add_cycle(self.big, 1, price="30.00", position=1)
        club, _ = self.billed()
        with at(SWITCH):
            result = pc.execute_plan_change(club, self.big)
        self.assertEqual((result.new_due.cycle.plan, result.new_due.cycle.duration_months, result.new_due.amount), (self.big, 12, M("300.00")))

    def test_changing_plan_and_cycle_together(self):
        monthly_big = add_cycle(self.big, 1, price="30.00", position=1)
        club, _ = self.billed()
        with at(SWITCH):
            result = pc.execute_plan_change(club, self.big, cycle=monthly_big)
        self.assertEqual((result.new_due.plan, result.new_due.cycle, result.new_due.amount), (self.big, monthly_big, M("30.00")))
        self.assertEqual(ClubBilling.objects.get(club=club).cycle, monthly_big)

    def test_a_cycle_without_a_price_on_the_day_is_refused_and_nothing_changes(self):
        unpriced = PlanCycle.objects.create(plan=self.plan, duration_months=3, position=2)
        club, due = self.billed()
        with at(SWITCH), self.assertRaises(BillingError):
            pc.execute_plan_change(club, self.plan, cycle=unpriced)
        due.refresh_from_db()
        self.assertFalse(due.is_prorated)
        self.assertEqual(Due.objects.filter(club=club).count(), 1)


class CycleApiTests(ApiBase):
    def put(self, path, body):
        with at(SWITCH):
            return self.client.put(path, json=body)

    def patch(self, path, body):
        with at(SWITCH):
            return self.client.patch(path, json=body)

    def delete(self, path):
        with at(SWITCH):
            return self.client.delete(path)

    def plan_row(self, plan):
        return next(p for p in self.get("/billing/plans").json() if p["plan"]["id"] == str(plan.pk))

    def test_plans_come_in_the_platform_order_with_their_cycles(self):
        feature_services.reorder_plans([self.small.pk, self.big.pk, self.plan.pk])
        rows = [p for p in self.get("/billing/plans").json() if p["plan"]["code"] in ("small-test", "big-test", "pro-test")]
        self.assertEqual([p["plan"]["code"] for p in rows], ["small-test", "big-test", "pro-test"])
        first = rows[0]["cycles"][0]
        self.assertEqual((first["name"], first["duration_months"], first["current_price"]), ("Yearly", 12, "40.00"))
        self.assertEqual([p["position"] for p in rows], [0, 1, 2])

    def test_the_plan_order_endpoint(self):
        ordered = [str(p.pk) for p in (self.big, self.small, self.plan)]
        r = self.put("/plans/order", {"plan_ids": ordered})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([p["id"] for p in r.json()[:3]], ordered)
        listed = self.client.get("/plans").json()["items"]
        self.assertEqual([p["id"] for p in listed[:3]], ordered)
        self.assertEqual(self.put("/plans/order", {"plan_ids": [ordered[0], ordered[0]]}).status_code, 422)  # listed twice

    def test_plans_left_out_of_the_order_follow_the_listed_ones(self):
        r = self.put("/plans/order", {"plan_ids": [str(self.small.pk)]})
        self.assertEqual(r.json()[0]["id"], str(self.small.pk))
        self.assertEqual(len(r.json()), Plan.objects.count())

    def test_add_edit_reorder_and_delete_cycles(self):
        added = self.post(f"/billing/plans/{self.big.pk}/cycles", {"duration_months": 1, "renewal_lead_days": 5, "grace_days": 10})
        self.assertEqual(added.status_code, 201, added.content)
        monthly = added.json()
        self.assertEqual((monthly["name"], monthly["position"], monthly["current_price"], monthly["is_active"]), ("Monthly", 1, None, True))
        self.assertEqual(self.post(f"/billing/plans/{self.big.pk}/cycles", {"duration_months": 1}).status_code, 409)  # that length is already offered
        self.assertEqual(self.post(f"/billing/plans/{self.big.pk}/cycles", {"duration_months": 1, "renewal_lead_days": 99}).status_code, 409)
        self.assertEqual(self.post(f"/billing/plans/{self.big.pk}/cycles", {"duration_months": 3, "renewal_lead_days": 99}).status_code, 422)  # lead longer than the period
        named = self.patch(f"/billing/cycles/{monthly['id']}", {"label": "Pay monthly", "grace_days": 14})
        self.assertEqual((named.json()["name"], named.json()["grace_days"]), ("Pay monthly", 14))
        yearly = self.plan_row(self.big)["cycles"][0]
        moved = self.put(f"/billing/plans/{self.big.pk}/cycles/order", {"cycle_ids": [monthly["id"], yearly["id"]]})
        self.assertEqual([c["name"] for c in moved.json()["cycles"]], ["Pay monthly", "Yearly"])
        self.assertEqual(self.put(f"/billing/plans/{self.big.pk}/cycles/order", {"cycle_ids": [monthly["id"], monthly["id"]]}).status_code, 422)
        self.assertEqual(self.delete(f"/billing/cycles/{monthly['id']}").status_code, 204)
        self.assertEqual([c["name"] for c in self.plan_row(self.big)["cycles"]], ["Yearly"])

    def test_the_last_cycle_stays_and_the_last_active_one_cannot_be_switched_off(self):
        only = self.plan_row(self.big)["cycles"][0]
        self.assertEqual(self.delete(f"/billing/cycles/{only['id']}").status_code, 409)
        self.assertEqual(self.patch(f"/billing/cycles/{only['id']}", {"is_active": False}).status_code, 409)

    def test_a_cycle_clubs_were_billed_on_is_deactivated_not_deleted(self):
        monthly = add_cycle(self.big, 1, price="30.00", position=1)
        club = make_club("Billed Monthly", billed=False)
        with at(START):
            dues_svc.subscribe(club, self.big, start=START, cycle=monthly)
        self.assertEqual(self.delete(f"/billing/cycles/{monthly.pk}").status_code, 409)
        self.assertEqual(self.patch(f"/billing/cycles/{monthly.pk}", {"is_active": False}).status_code, 200)

    def test_prices_belong_to_a_cycle(self):
        monthly = add_cycle(self.big, 1, price="30.00", position=1)
        r = self.post(f"/billing/cycles/{monthly.pk}/prices", {"active_from": "2026-09-01", "amount": "35.00"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.post(f"/billing/cycles/{monthly.pk}/prices", {"active_from": "2026-09-01", "amount": "36.00"}).status_code, 409)
        cycles = {c["name"]: c for c in self.plan_row(self.big)["cycles"]}
        self.assertEqual((cycles["Monthly"]["current_price"], len(cycles["Monthly"]["prices"]), cycles["Yearly"]["current_price"]), ("30.00", 2, "300.00"))

    def test_start_billing_on_a_cycle(self):
        monthly = add_cycle(self.big, 1, price="30.00", position=1)
        club = make_club("Api Monthly", billed=False)
        r = self.post(f"/clubs/{club.pk}/billing/subscribe", {"plan_id": str(self.big.pk), "cycle_id": str(monthly.pk), "start": "2026-07-02"})
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual((body["cycle"]["name"], body["dues"][0]["cycle"]["name"], body["dues"][0]["period_end"], body["dues"][0]["amount"]), ("Monthly", "Monthly", "2026-08-01", "30.00"))
        wrong = self.post(f"/clubs/{make_club('Api Wrong', billed=False).pk}/billing/subscribe", {"plan_id": str(self.big.pk), "cycle_id": str(self.plan.cycles.get().pk)})
        self.assertEqual(wrong.status_code, 422)

    def test_changing_cycle_previews_what_it_then_does(self):
        monthly = add_cycle(self.plan, 1, price="20.00", position=1)
        club, _ = self.billed(paid=M("121.00"))
        base = f"/clubs/{club.pk}/billing/change-plan"
        body = {"plan_id": str(self.plan.pk), "cycle_id": str(monthly.pk)}
        preview = self.post(base + "/preview", body)
        self.assertEqual(preview.status_code, 200, preview.content)
        done = self.post(base, body)
        self.assertEqual(done.status_code, 201, done.content)
        self.assertEqual((preview.json()["from_cycle"]["name"], preview.json()["to_cycle"]["name"], preview.json()["new_period"]["cycle"]["name"]), ("Yearly", "Monthly", "Monthly"))
        for key in preview.json():
            self.assertEqual(done.json()[key], preview.json()[key], key)
        self.assertEqual(self.post(base + "/preview", {"plan_id": str(self.plan.pk)}).status_code, 409)  # already on this plan and cycle
