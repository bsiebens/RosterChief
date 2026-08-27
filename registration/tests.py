import datetime
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from authentication.models import User
from club.models import Club, ClubMembership, Season
from club.services.fees import effective_fee_amount, record_payment, remaining_balance
from members.models import Family, FamilyMembership, Member
from shop.models import Product, ProductRegistrantDiscountTier, ProductVariant
from teams.models import Position, Team

from .models import RegistrationBatch, RegistrationDetails
from .services import EntryInput, PricingError, RegistrationError, available_registration_products, price_entries, resolve_registration_season, submit_registration


def make_club(**kwargs):
    defaults = {"name": "Ajax United", "slug": "ajax-united"}
    defaults.update(kwargs)
    return Club.objects.create(**defaults)


def make_season(club, start=None, end=None):
    start = start or datetime.date(2026, 8, 1)
    end = end or datetime.date(2027, 6, 30)
    return Season.objects.create(club=club, start_date=start, end_date=end)


class PricingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.season = make_season(cls.club)
        cls.product = Product.objects.create(club=cls.club, name="Player Registration", product_type=Product.ProductType.MEMBERSHIP, season=cls.season, price=Decimal("100.00"))
        cls.u10 = ProductVariant.objects.create(product=cls.product, name="U10", price=Decimal("80.00"))
        cls.u12 = ProductVariant.objects.create(product=cls.product, name="U12", price=Decimal("90.00"))

    def test_price_entries_uses_the_variants_own_effective_price(self):
        rows = price_entries([self.u10, self.u12])

        self.assertEqual(rows[0]["price"], Decimal("80.00"))
        self.assertEqual(rows[1]["price"], Decimal("90.00"))

    def test_a_none_entry_is_free_with_no_discount(self):
        rows = price_entries([None])

        self.assertEqual(rows[0]["price"], Decimal("0"))
        self.assertEqual(rows[0]["min_registrants_discount"], Decimal("0"))
        self.assertIsNone(rows[0]["deadline"])

    def test_available_registration_products_includes_event_fee_not_merchandise(self):
        event_fee = Product.objects.create(club=self.club, name="Summer Camp", product_type=Product.ProductType.EVENT_FEE, season=self.season, price=Decimal("50.00"))
        Product.objects.create(club=self.club, name="Home Jersey", product_type=Product.ProductType.MERCHANDISE, season=self.season, price=Decimal("30.00"))

        available = list(available_registration_products(self.club))

        self.assertIn(self.product, available)
        self.assertIn(event_fee, available)

    def test_available_registration_products_excludes_a_completed_season(self):
        past_season = make_season(self.club, start=datetime.date(2024, 8, 1), end=datetime.date(2025, 6, 30))
        ended = Product.objects.create(club=self.club, name="Old Registration", product_type=Product.ProductType.MEMBERSHIP, season=past_season, price=Decimal("50.00"))

        available = list(available_registration_products(self.club))

        self.assertIn(self.product, available)
        self.assertNotIn(ended, available)

    def test_min_registrants_discount_applies_once_the_threshold_is_met(self):
        ProductRegistrantDiscountTier.objects.create(product=self.product, min_registrants=2, discount_type="percentage", discount_amount=Decimal("10"))

        below_threshold = price_entries([self.u10])
        at_threshold = price_entries([self.u10, self.u12])

        self.assertEqual(below_threshold[0]["min_registrants_discount"], Decimal("0"))
        self.assertEqual(at_threshold[0]["min_registrants_discount"], Decimal("8.00"))  # 10% of 80

    def test_min_registrants_only_counts_entries_of_the_same_product(self):
        other_product = Product.objects.create(club=self.club, name="Volunteer Registration", product_type=Product.ProductType.MEMBERSHIP, season=self.season, price=Decimal("0"))
        other_variant = ProductVariant.objects.create(product=other_product, name="Coach", price=Decimal("0"))
        ProductRegistrantDiscountTier.objects.create(product=self.product, min_registrants=2, discount_type="percentage", discount_amount=Decimal("10"))

        rows = price_entries([self.u10, other_variant])

        self.assertEqual(rows[0]["min_registrants_discount"], Decimal("0"))

    def test_the_best_qualifying_tier_applies_not_just_the_first(self):
        # x people = x% off, y people = y% off -- a staircase, not a single
        # threshold. Three tiers: 2+ -> 5%, 3+ -> 10%, 5+ -> 15%.
        ProductRegistrantDiscountTier.objects.create(product=self.product, min_registrants=2, discount_type="percentage", discount_amount=Decimal("5"))
        ProductRegistrantDiscountTier.objects.create(product=self.product, min_registrants=3, discount_type="percentage", discount_amount=Decimal("10"))
        ProductRegistrantDiscountTier.objects.create(product=self.product, min_registrants=5, discount_type="percentage", discount_amount=Decimal("15"))
        u10_c = ProductVariant.objects.create(product=self.product, name="U10-C", price=Decimal("80.00"))

        two_people = price_entries([self.u10, self.u12])
        three_people = price_entries([self.u10, self.u12, u10_c])

        self.assertEqual(two_people[0]["min_registrants_discount"], Decimal("4.00"))  # 5% of 80
        self.assertEqual(three_people[0]["min_registrants_discount"], Decimal("8.00"))  # 10% of 80, not 5%

    def test_a_tier_never_applies_below_its_own_threshold(self):
        ProductRegistrantDiscountTier.objects.create(product=self.product, min_registrants=5, discount_type="percentage", discount_amount=Decimal("15"))

        rows = price_entries([self.u10, self.u12])

        self.assertEqual(rows[0]["min_registrants_discount"], Decimal("0"))

    def test_early_bird_discount_is_reported_but_not_applied_to_price(self):
        deadline = datetime.date(2026, 8, 15)
        self.product.early_bird_discount_enabled = True
        self.product.early_bird_discount_deadline = deadline
        self.product.early_bird_discount_type = "fixed_amount"
        self.product.early_bird_discount_amount = Decimal("15.00")
        self.product.save()

        rows = price_entries([self.u10])

        self.assertEqual(rows[0]["price"], Decimal("80.00"))
        self.assertEqual(rows[0]["deadline"], deadline)
        self.assertEqual(rows[0]["deadline_discount"], Decimal("15.00"))

    def test_resolve_registration_season_requires_a_single_season(self):
        other_season = make_season(self.club, start=datetime.date(2027, 8, 1), end=datetime.date(2028, 6, 30))
        other_product = Product.objects.create(club=self.club, name="Other", product_type=Product.ProductType.MEMBERSHIP, season=other_season, price=Decimal("50"))
        other_variant = ProductVariant.objects.create(product=other_product, name="Only")

        with self.assertRaises(PricingError):
            resolve_registration_season([self.u10, other_variant])

    def test_resolve_registration_season_returns_the_shared_season(self):
        season = resolve_registration_season([self.u10, self.u12])

        self.assertEqual(season, self.season)


class SubmitRegistrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.season = make_season(cls.club)
        cls.player_product = Product.objects.create(club=cls.club, name="Player Registration", product_type=Product.ProductType.MEMBERSHIP, season=cls.season, price=Decimal("100.00"))
        cls.u10 = ProductVariant.objects.create(product=cls.player_product, name="U10", price=Decimal("80.00"))
        cls.volunteer_product = Product.objects.create(club=cls.club, name="Volunteer Registration", product_type=Product.ProductType.MEMBERSHIP, season=cls.season, price=Decimal("0"))
        cls.coach_variant = ProductVariant.objects.create(product=cls.volunteer_product, name="Coach", price=Decimal("0"))
        cls.team = Team.objects.create(club=cls.club, name="U10 Boys", short_name="U10B")
        cls.position = Position.objects.create(club=cls.club, name="Coach", short_name="CO", staff_position=True)

    def test_a_parent_registering_one_child_creates_a_pending_membership(self):
        entries = [EntryInput(first_name="Timmy", last_name="Tester", date_of_birth=datetime.date(2016, 5, 1), product_variant=self.u10, requested_team=self.team)]

        batch = submit_registration(self.club, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com", entries=entries)

        self.assertEqual(RegistrationBatch.objects.count(), 1)
        self.assertEqual(batch.total, Decimal("80.00"))
        child = Member.objects.get(first_name="Timmy", last_name="Tester")
        membership = ClubMembership.objects.get(club=self.club, member=child, season=self.season)
        self.assertEqual(membership.status, ClubMembership.StatusChoices.PENDING)
        self.assertEqual(membership.fee_amount, Decimal("80.00"))
        self.assertEqual(membership.fee_status, ClubMembership.FeeStatus.UNPAID)
        details = RegistrationDetails.objects.get(membership=membership)
        self.assertEqual(details.entry_kind, RegistrationDetails.EntryKind.PLAYER)
        self.assertEqual(details.requested_team, self.team)

    def test_the_contact_becomes_a_guardian_with_no_fee(self):
        entries = [EntryInput(first_name="Timmy", last_name="Tester", date_of_birth=datetime.date(2016, 5, 1), product_variant=self.u10)]

        submit_registration(self.club, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com", entries=entries)

        parent = Member.objects.get(first_name="Pat", last_name="Parent")
        parent_membership = ClubMembership.objects.get(club=self.club, member=parent, season=self.season)
        self.assertEqual(parent_membership.kind, ClubMembership.Kind.GUARDIAN)
        self.assertEqual(parent_membership.status, ClubMembership.StatusChoices.ACTIVE)
        self.assertEqual(parent_membership.fee_amount, Decimal("0.00"))
        child = Member.objects.get(first_name="Timmy")
        self.assertEqual(child.family_memberships.get().family, parent.family_memberships.get().family)

    def test_a_self_registering_adult_gets_their_own_pending_membership_no_guardian_row(self):
        entries = [EntryInput(first_name="Pat", last_name="Parent", product_variant=self.u10, is_contact=True)]

        submit_registration(self.club, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com", entries=entries)

        parent = Member.objects.get(first_name="Pat", last_name="Parent")
        membership = ClubMembership.objects.get(club=self.club, member=parent, season=self.season)
        self.assertEqual(membership.kind, ClubMembership.Kind.MEMBER)
        self.assertEqual(membership.status, ClubMembership.StatusChoices.PENDING)
        self.assertEqual(membership.fee_amount, Decimal("80.00"))

    def test_a_volunteer_entry_records_the_requested_position_team_optional(self):
        entries = [EntryInput(first_name="Val", last_name="Volunteer", entry_kind=RegistrationDetails.EntryKind.VOLUNTEER, product_variant=self.coach_variant, requested_position=self.position, is_contact=True)]

        submit_registration(self.club, contact_first_name="Val", contact_last_name="Volunteer", contact_email="val@example.com", entries=entries)

        member = Member.objects.get(first_name="Val")
        membership = ClubMembership.objects.get(club=self.club, member=member, season=self.season)
        details = RegistrationDetails.objects.get(membership=membership)
        self.assertEqual(details.entry_kind, RegistrationDetails.EntryKind.VOLUNTEER)
        self.assertIsNone(details.requested_team)
        self.assertEqual(details.requested_position, self.position)

    def test_a_player_entry_never_records_a_requested_position(self):
        entries = [EntryInput(first_name="Timmy", last_name="Tester", product_variant=self.u10, requested_position=self.position)]

        submit_registration(self.club, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com", entries=entries)

        details = RegistrationDetails.objects.get(membership__member__first_name="Timmy")
        self.assertIsNone(details.requested_position)

    def test_min_registrants_discount_reduces_fee_amount_immediately(self):
        ProductRegistrantDiscountTier.objects.create(product=self.player_product, min_registrants=2, discount_type="percentage", discount_amount=Decimal("10"))
        u12 = ProductVariant.objects.create(product=self.player_product, name="U12", price=Decimal("90.00"))
        entries = [
            EntryInput(first_name="Timmy", last_name="Tester", product_variant=self.u10),
            EntryInput(first_name="Jamie", last_name="Tester", product_variant=u12),
        ]

        batch = submit_registration(self.club, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com", entries=entries)

        self.assertEqual(batch.subtotal, Decimal("170.00"))
        self.assertEqual(batch.discount_amount, Decimal("17.00"))
        self.assertEqual(batch.total, Decimal("153.00"))
        timmy_membership = ClubMembership.objects.get(member__first_name="Timmy")
        self.assertEqual(timmy_membership.fee_amount, Decimal("72.00"))  # 80 - 10%

    def test_early_bird_discount_is_stored_on_the_membership_not_applied_now(self):
        deadline = datetime.date(2026, 9, 1)
        self.player_product.early_bird_discount_enabled = True
        self.player_product.early_bird_discount_deadline = deadline
        self.player_product.early_bird_discount_type = "fixed_amount"
        self.player_product.early_bird_discount_amount = Decimal("10.00")
        self.player_product.save()
        entries = [EntryInput(first_name="Timmy", last_name="Tester", product_variant=self.u10)]

        submit_registration(self.club, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com", entries=entries)

        membership = ClubMembership.objects.get(member__first_name="Timmy")
        self.assertEqual(membership.fee_amount, Decimal("80.00"))
        self.assertEqual(membership.early_payment_deadline, deadline)
        self.assertEqual(membership.early_payment_discount, Decimal("10.00"))

    def test_an_existing_member_is_reused_not_duplicated(self):
        existing_child = Member.objects.create(first_name="Timmy", last_name="Tester", date_of_birth=datetime.date(2016, 5, 1))
        entries = [EntryInput(existing_member=existing_child, product_variant=self.u10)]

        submit_registration(self.club, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com", entries=entries)

        self.assertEqual(Member.objects.filter(first_name="Timmy").count(), 1)
        membership = ClubMembership.objects.get(member=existing_child, season=self.season)
        self.assertEqual(membership.status, ClubMembership.StatusChoices.PENDING)

    def test_a_returning_contact_is_matched_by_email_not_forked_into_a_new_family(self):
        parent_user = User.objects.create_user(email="pat@example.com", password="pw-secret-123")
        parent = Member.objects.create(user=parent_user, first_name="Pat", last_name="Parent")
        FamilyMembership.objects.create(family=Family.objects.create(), member=parent, role=FamilyMembership.FamilyRole.PARENT)
        entries = [EntryInput(first_name="Timmy", last_name="Tester", product_variant=self.u10)]

        submit_registration(self.club, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com", entries=entries)

        self.assertEqual(Member.objects.filter(first_name="Pat", last_name="Parent").count(), 1)
        child = Member.objects.get(first_name="Timmy")
        self.assertEqual(child.family_memberships.get().family, parent.family_memberships.get().family)

    def test_an_authenticated_submitter_is_matched_by_login_not_email(self):
        user = User.objects.create_user(email="different-login-email@example.com", password="pw-secret-123")
        parent = Member.objects.create(user=user, first_name="Pat", last_name="Parent")
        entries = [EntryInput(first_name="Timmy", last_name="Tester", product_variant=self.u10)]

        batch = submit_registration(self.club, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com", entries=entries, submitted_by_user=user)

        self.assertEqual(batch.submitted_by_user, user)
        self.assertEqual(Member.objects.filter(first_name="Pat", last_name="Parent").count(), 1)
        child = Member.objects.get(first_name="Timmy")
        self.assertEqual(child.family_memberships.get().family, parent.family_memberships.get().family)

    def test_no_entries_raises(self):
        with self.assertRaises(RegistrationError):
            submit_registration(self.club, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com", entries=[])

    def test_mismatched_seasons_across_entries_raises(self):
        other_season = make_season(self.club, start=datetime.date(2027, 8, 1), end=datetime.date(2028, 6, 30))
        other_product = Product.objects.create(club=self.club, name="Other season", product_type=Product.ProductType.MEMBERSHIP, season=other_season, price=Decimal("50"))
        other_variant = ProductVariant.objects.create(product=other_product, name="Only")
        entries = [EntryInput(first_name="Timmy", last_name="Tester", product_variant=self.u10), EntryInput(first_name="Jamie", last_name="Tester", product_variant=other_variant)]

        with self.assertRaises(RegistrationError):
            submit_registration(self.club, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com", entries=entries)


class EarlyPaymentDeadlineFeeTests(TestCase):
    """club.services.fees' extension for a discount that isn't confirmed
    until the fee is actually paid -- see registration.services.pricing's
    own docstring for why this couldn't just be baked into fee_amount."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.season = make_season(cls.club)
        cls.member = Member.objects.create(first_name="Timmy", last_name="Tester")

    def make_membership(self, **kwargs):
        defaults = {"club": self.club, "member": self.member, "season": self.season, "fee_amount": Decimal("80.00")}
        defaults.update(kwargs)
        return ClubMembership.objects.create(**defaults)

    def test_no_deadline_set_is_unaffected(self):
        membership = self.make_membership()

        self.assertEqual(effective_fee_amount(membership), Decimal("80.00"))

    def test_within_the_deadline_the_discount_applies(self):
        membership = self.make_membership(early_payment_deadline=datetime.date(2026, 9, 1), early_payment_discount=Decimal("10.00"))

        self.assertEqual(effective_fee_amount(membership, when=datetime.date(2026, 8, 20)), Decimal("70.00"))

    def test_past_the_deadline_the_full_amount_is_owed(self):
        membership = self.make_membership(early_payment_deadline=datetime.date(2026, 9, 1), early_payment_discount=Decimal("10.00"))

        self.assertEqual(effective_fee_amount(membership, when=datetime.date(2026, 9, 2)), Decimal("80.00"))

    def test_remaining_balance_reflects_the_deadline(self):
        membership = self.make_membership(early_payment_deadline=datetime.date(2026, 9, 1), early_payment_discount=Decimal("10.00"))

        self.assertEqual(remaining_balance(membership, when=datetime.date(2026, 8, 20)), Decimal("70.00"))
        self.assertEqual(remaining_balance(membership, when=datetime.date(2026, 9, 2)), Decimal("80.00"))

    def test_paying_the_discounted_amount_before_the_deadline_resolves_to_paid(self):
        membership = self.make_membership(early_payment_deadline=datetime.date(2026, 9, 1), early_payment_discount=Decimal("10.00"))

        record_payment(membership, amount=Decimal("70.00"))

        membership.refresh_from_db()
        self.assertEqual(membership.fee_status, ClubMembership.FeeStatus.PAID)

    def test_paying_only_the_discounted_amount_after_the_deadline_is_only_partial(self):
        membership = self.make_membership(early_payment_deadline=datetime.date(2020, 1, 1), early_payment_discount=Decimal("10.00"))

        record_payment(membership, amount=Decimal("70.00"))

        membership.refresh_from_db()
        self.assertEqual(membership.fee_status, ClubMembership.FeeStatus.PARTIALLY_PAID)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class RegistrationViewTests(TestCase):
    """registration:register -- the public, unauthenticated registration
    page (registration.views.RegistrationView)."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.season = make_season(cls.club)
        cls.product = Product.objects.create(club=cls.club, name="Player Registration", product_type=Product.ProductType.MEMBERSHIP, season=cls.season, price=Decimal("100.00"))
        cls.u10 = ProductVariant.objects.create(product=cls.product, name="U10", price=Decimal("80.00"))
        cls.team = Team.objects.create(club=cls.club, name="U10 Boys", short_name="U10B")

    def _url(self):
        return reverse("registration:register")

    def formset_management(self, total=1, prefix="entries"):
        return {f"{prefix}-TOTAL_FORMS": str(total), f"{prefix}-INITIAL_FORMS": "0", f"{prefix}-MIN_NUM_FORMS": "0", f"{prefix}-MAX_NUM_FORMS": "1000"}

    def entry_data(self, index=0, prefix="entries", **overrides):
        data = {
            f"{prefix}-{index}-first_name": "Timmy",
            f"{prefix}-{index}-last_name": "Tester",
            f"{prefix}-{index}-date_of_birth": "2016-05-01",
            f"{prefix}-{index}-entry_kind": RegistrationDetails.EntryKind.PLAYER,
            f"{prefix}-{index}-product_variant": str(self.u10.pk),
        }
        data.update({f"{prefix}-{index}-{key}": value for key, value in overrides.items()})
        return data

    def contact_data(self, **overrides):
        data = {"contact_first_name": "Pat", "contact_last_name": "Parent", "contact_email": "pat@example.com", "contact_phone": ""}
        data.update(overrides)
        return data

    def test_get_renders_the_form(self):
        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Register")

    def test_no_registration_products_shows_an_empty_state(self):
        self.product.is_active = False
        self.product.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "isn't open right now")

    def test_calculating_the_price_does_not_create_any_records(self):
        data = self.contact_data() | self.formset_management() | self.entry_data()
        data["action"] = "preview"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "80.00")
        self.assertFalse(RegistrationBatch.objects.exists())
        self.assertFalse(Member.objects.filter(first_name="Timmy").exists())

    def test_submitting_creates_the_registration(self):
        data = self.contact_data() | self.formset_management() | self.entry_data()
        data["action"] = "submit"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, self._url())
        self.assertEqual(RegistrationBatch.objects.count(), 1)
        child = Member.objects.get(first_name="Timmy", last_name="Tester")
        membership = ClubMembership.objects.get(club=self.club, member=child, season=self.season)
        self.assertEqual(membership.status, ClubMembership.StatusChoices.PENDING)
        self.assertEqual(membership.fee_amount, Decimal("80.00"))

    def test_a_missing_last_name_is_rejected(self):
        data = self.contact_data() | self.formset_management() | self.entry_data(last_name="")
        data["action"] = "preview"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(RegistrationBatch.objects.exists())

    def test_an_empty_formset_is_rejected(self):
        data = self.contact_data() | self.formset_management()
        data["action"] = "preview"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Register at least one person.")

    def test_an_authenticated_submitter_sees_locked_contact_fields_and_is_used_as_the_contact(self):
        user = User.objects.create_user(email="pat@example.com", password="pw-secret-123")
        Member.objects.create(user=user, first_name="Pat", last_name="Parent")
        self.client.force_login(user)

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "Submitting as:")

        data = self.formset_management() | self.entry_data()
        data["action"] = "submit"
        self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        batch = RegistrationBatch.objects.get()
        self.assertEqual(batch.submitted_by_user, user)
        self.assertEqual(Member.objects.filter(first_name="Pat", last_name="Parent").count(), 1)
