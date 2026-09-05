import datetime
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from authentication.models import User
from club.models import Club, ClubMembership, MemberRequirementStatus, OnboardingRequirement, Season
from club.services.fees import effective_fee_amount, record_payment, remaining_balance
from members.models import Family, FamilyMembership, Member
from shop.models import Product, ProductCategory, ProductRegistrantDiscountTier, ProductVariant
from teams.models import NumberPool, Position, Team, TeamMembership

from .models import RegistrationBatch, RegistrationDetails
from .services import EntryInput, PricingError, RegistrationError, available_registration_products, jersey_choices_for_entry, price_entries, resolve_registration_season, submit_registration
from .services.invoicing import RegistrationInvoicePDFError


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

    def test_per_person_scope_applies_the_tier_to_every_qualifying_entry(self):
        # Product.registrant_discount_scope defaults to PER_PERSON.
        ProductRegistrantDiscountTier.objects.create(product=self.product, min_registrants=2, discount_type="percentage", discount_amount=Decimal("10"))
        u10_c = ProductVariant.objects.create(product=self.product, name="U10-C", price=Decimal("80.00"))

        rows = price_entries([self.u10, u10_c])

        self.assertEqual(rows[0]["min_registrants_discount"], Decimal("8.00"))
        self.assertEqual(rows[1]["min_registrants_discount"], Decimal("8.00"))

    def test_per_order_scope_applies_the_tier_once_not_per_entry(self):
        self.product.registrant_discount_scope = Product.RegistrantDiscountScope.PER_ORDER
        self.product.save()
        ProductRegistrantDiscountTier.objects.create(product=self.product, min_registrants=2, discount_type="percentage", discount_amount=Decimal("10"))
        u10_c = ProductVariant.objects.create(product=self.product, name="U10-C", price=Decimal("80.00"))

        rows = price_entries([self.u10, u10_c])

        self.assertEqual(rows[0]["min_registrants_discount"], Decimal("8.00"))
        self.assertEqual(rows[1]["min_registrants_discount"], Decimal("0"))

    def test_per_order_scope_is_independent_per_product(self):
        self.product.registrant_discount_scope = Product.RegistrantDiscountScope.PER_ORDER
        self.product.save()
        ProductRegistrantDiscountTier.objects.create(product=self.product, min_registrants=2, discount_type="percentage", discount_amount=Decimal("10"))
        other_product = Product.objects.create(
            club=self.club, name="Volunteer Registration", product_type=Product.ProductType.MEMBERSHIP, season=self.season, price=Decimal("60.00"), registrant_discount_scope=Product.RegistrantDiscountScope.PER_PERSON
        )
        ProductRegistrantDiscountTier.objects.create(product=other_product, min_registrants=2, discount_type="percentage", discount_amount=Decimal("10"))
        other_variant_1 = ProductVariant.objects.create(product=other_product, name="Coach A", price=Decimal("60.00"))
        other_variant_2 = ProductVariant.objects.create(product=other_product, name="Coach B", price=Decimal("60.00"))
        u10_c = ProductVariant.objects.create(product=self.product, name="U10-C", price=Decimal("80.00"))

        rows = price_entries([self.u10, u10_c, other_variant_1, other_variant_2])

        self.assertEqual(rows[0]["min_registrants_discount"], Decimal("8.00"))  # per-order: once
        self.assertEqual(rows[1]["min_registrants_discount"], Decimal("0"))
        self.assertEqual(rows[2]["min_registrants_discount"], Decimal("6.00"))  # per-person: every time
        self.assertEqual(rows[3]["min_registrants_discount"], Decimal("6.00"))

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


class JerseyChoicesForEntryTests(TestCase):
    """registration.services.pricing.jersey_choices_for_entry -- the
    team-scoped narrowing used once an entry is already priced (see
    RegistrationJerseyNumberTests/ReRegisterJerseyNumberTests for the
    view-level "field only appears in the price panel" behaviour this
    feeds)."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.team = Team.objects.create(club=cls.club, name="U10 Boys", short_name="U10B")

    def entry(self, **overrides):
        defaults = {"entry_kind": "player", "requested_team": self.team, "existing_member": None}
        defaults.update(overrides)
        return EntryInput(**defaults)

    def test_none_for_a_volunteer_entry(self):
        result = jersey_choices_for_entry(self.entry(entry_kind="volunteer"), {str(self.team.pk): [1, 2, 3]})

        self.assertIsNone(result)

    def test_none_with_no_requested_team(self):
        result = jersey_choices_for_entry(self.entry(requested_team=None), {str(self.team.pk): [1, 2, 3]})

        self.assertIsNone(result)

    def test_none_when_the_team_has_no_numbers_at_all(self):
        result = jersey_choices_for_entry(self.entry(), {})

        self.assertIsNone(result)

    def test_scoped_to_just_this_entrys_own_team(self):
        other_team = Team.objects.create(club=self.club, name="Senior A", short_name="SA")
        result = jersey_choices_for_entry(self.entry(), {str(self.team.pk): [1, 2, 3], str(other_team.pk): [8, 9]})

        self.assertEqual(result, [1, 2, 3])

    def test_adds_back_the_members_own_current_number(self):
        member = Member.objects.create(first_name="Lars", last_name="Bakker")
        result = jersey_choices_for_entry(
            self.entry(existing_member=member),
            {str(self.team.pk): [1, 2, 3]},
            member_current_numbers={str(member.pk): {str(self.team.pk): 7}},
        )

        self.assertEqual(result, [1, 2, 3, 7])

    def test_no_member_current_numbers_is_fine(self):
        result = jersey_choices_for_entry(self.entry(), {str(self.team.pk): [1, 2, 3]}, member_current_numbers=None)

        self.assertEqual(result, [1, 2, 3])


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

    def test_a_second_registration_for_the_same_member_and_season_adds_a_new_request(self):
        second_team = Team.objects.create(club=self.club, name="U10 Girls", short_name="U10G")
        existing_child = Member.objects.create(first_name="Timmy", last_name="Tester", date_of_birth=datetime.date(2016, 5, 1))
        first_membership = ClubMembership.objects.create(
            club=self.club, member=existing_child, season=self.season, status=ClubMembership.StatusChoices.ACTIVE, fee_amount=Decimal("80.00"), fee_status=ClubMembership.FeeStatus.PAID, amount_paid=Decimal("80.00")
        )
        first_batch = RegistrationBatch.objects.create(club=self.club, season=self.season, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com")
        RegistrationDetails.objects.create(membership=first_membership, batch=first_batch, entry_kind=RegistrationDetails.EntryKind.PLAYER, requested_team=self.team, product_variant=self.u10, price=Decimal("80.00"))

        # Playing on a second team, on top of the first (already ACTIVE and
        # PAID) registration -- not a duplicate ClubMembership (unique per
        # club/member/season), a second request against the same one.
        entries = [EntryInput(existing_member=existing_child, product_variant=self.u10, requested_team=second_team)]
        submit_registration(self.club, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com", entries=entries)

        self.assertEqual(ClubMembership.objects.filter(club=self.club, member=existing_child, season=self.season).count(), 1)
        membership = ClubMembership.objects.get(club=self.club, member=existing_child, season=self.season)
        self.assertEqual(membership.fee_amount, Decimal("160.00"))  # 80 (first) + 80 (second)
        self.assertEqual(membership.status, ClubMembership.StatusChoices.PENDING)  # back in the queue -- there's a new, unplaced request
        self.assertEqual(membership.fee_status, ClubMembership.FeeStatus.PARTIALLY_PAID)  # 80 paid against a now-160 total
        self.assertEqual(RegistrationDetails.objects.filter(membership=membership).count(), 2)
        self.assertEqual({details.requested_team for details in RegistrationDetails.objects.filter(membership=membership)}, {self.team, second_team})

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
        # record_payment has no `when` param -- it evaluates the deadline against the
        # real paid_at, so unlike the other tests here (which pass `when=` explicitly)
        # this deadline has to be a real future date, not a fixed literal that rots
        # into the past as real time passes.
        membership = self.make_membership(early_payment_deadline=timezone.localdate() + datetime.timedelta(days=10), early_payment_discount=Decimal("10.00"))

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
            f"{prefix}-{index}-requested_team": str(self.team.pk),
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

    def test_the_form_no_longer_asks_for_a_role(self):
        # Role is implied by the chosen product now, not asked for directly.
        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotIn("requested_position", response.context["entry_formset"].forms[0].fields)
        self.assertNotContains(response, "entries-0-requested_position")

    def test_two_entries_both_claiming_to_be_the_submitter_is_rejected(self):
        # The public page's is_contact genuinely means "this is me" -- at
        # most one row can claim it. Unlike mobile's own "Include this
        # person" reuse of the same field (see ReRegisterViewTests'
        # equivalent, which must NOT hit this).
        data = self.contact_data() | self.formset_management(2) | self.entry_data(0, is_contact="on") | self.entry_data(1, first_name="Jamie", is_contact="on")
        data["action"] = "preview"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only one entry can be")

    def test_no_registration_products_shows_an_empty_state(self):
        self.product.is_active = False
        self.product.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "isn't open right now")

    def test_the_single_available_season_shows_clearly_and_is_auto_chosen(self):
        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        # "Season 2026-2027", not Season.name's short "26-27" form.
        self.assertContains(response, f"Season {self.season.start_date.year}-{self.season.end_date.year}")
        self.assertNotContains(response, "Which season?")
        self.assertEqual(response.context["season"], self.season)

    def test_two_open_seasons_shows_a_picker_instead_of_the_form(self):
        other_season = make_season(self.club, start=datetime.date(2027, 8, 1), end=datetime.date(2028, 6, 30))
        other_product = Product.objects.create(club=self.club, name="Player Registration 27-28", product_type=Product.ProductType.MEMBERSHIP, season=other_season, price=Decimal("100.00"))
        ProductVariant.objects.create(product=other_product, name="U10", price=Decimal("85.00"))

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "Which season?")
        self.assertNotContains(response, 'name="entries-TOTAL_FORMS"')
        self.assertEqual(set(response.context["available_seasons"]), {self.season, other_season})

    def test_picking_a_season_scopes_the_variant_choices(self):
        other_season = make_season(self.club, start=datetime.date(2027, 8, 1), end=datetime.date(2028, 6, 30))
        other_product = Product.objects.create(club=self.club, name="Player Registration 27-28", product_type=Product.ProductType.MEMBERSHIP, season=other_season, price=Decimal("100.00"))
        other_variant = ProductVariant.objects.create(product=other_product, name="U10", price=Decimal("85.00"))

        response = self.client.get(self._url(), {"season": str(self.season.pk)}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["season"], self.season)
        variant_field = response.context["entry_formset"].forms[0].fields["product_variant"]
        self.assertIn(self.u10, variant_field.queryset)
        self.assertNotIn(other_variant, variant_field.queryset)

    def test_submitting_with_two_open_seasons_uses_the_hidden_season_field(self):
        other_season = make_season(self.club, start=datetime.date(2027, 8, 1), end=datetime.date(2028, 6, 30))
        other_product = Product.objects.create(club=self.club, name="Player Registration 27-28", product_type=Product.ProductType.MEMBERSHIP, season=other_season, price=Decimal("100.00"))
        ProductVariant.objects.create(product=other_product, name="U10", price=Decimal("85.00"))
        data = self.contact_data() | self.formset_management() | self.entry_data() | {"season": str(self.season.pk), "action": "submit"}

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        batch = RegistrationBatch.objects.get()
        self.assertRedirects(response, reverse("registration:status", kwargs={"token": batch.status_token}))
        child = Member.objects.get(first_name="Timmy", last_name="Tester")
        self.assertTrue(ClubMembership.objects.filter(club=self.club, member=child, season=self.season).exists())

    def test_calculating_the_price_does_not_create_any_records(self):
        data = self.contact_data() | self.formset_management() | self.entry_data()
        data["action"] = "preview"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "80.00")
        self.assertFalse(RegistrationBatch.objects.exists())
        self.assertFalse(Member.objects.filter(first_name="Timmy").exists())

    def test_the_price_panel_shows_a_total(self):
        data = self.contact_data() | self.formset_management() | self.entry_data()
        data["action"] = "preview"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "Total")
        self.assertEqual(response.context["priced_total"], Decimal("80.00"))

    def test_the_price_panel_is_an_empty_state_before_calculating(self):
        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "Fill in who")
        self.assertIsNone(response.context["priced_total"])

    def test_the_price_panel_shows_an_early_payment_total_when_a_deadline_applies(self):
        self.product.early_bird_discount_enabled = True
        self.product.early_bird_discount_deadline = datetime.date(2099, 1, 1)
        self.product.early_bird_discount_type = "fixed_amount"
        self.product.early_bird_discount_amount = Decimal("10.00")
        self.product.save()
        data = self.contact_data() | self.formset_management() | self.entry_data()
        data["action"] = "preview"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "If paid before the deadline")
        self.assertEqual(response.context["priced_total"], Decimal("80.00"))
        self.assertEqual(response.context["priced_early_total"], Decimal("70.00"))

    def test_no_early_payment_total_line_without_a_deadline(self):
        data = self.contact_data() | self.formset_management() | self.entry_data()
        data["action"] = "preview"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotContains(response, "If paid before the deadline")
        self.assertEqual(response.context["priced_early_total"], response.context["priced_total"])

    def test_submitting_creates_the_registration(self):
        data = self.contact_data() | self.formset_management() | self.entry_data()
        data["action"] = "submit"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(RegistrationBatch.objects.count(), 1)
        batch = RegistrationBatch.objects.get()
        self.assertRedirects(response, reverse("registration:status", kwargs={"token": batch.status_token}))
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

    def test_team_is_optional(self):
        data = self.contact_data() | self.formset_management() | self.entry_data(requested_team="")
        data["action"] = "submit"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        batch = RegistrationBatch.objects.get()
        self.assertRedirects(response, reverse("registration:status", kwargs={"token": batch.status_token}))
        child = Member.objects.get(first_name="Timmy", last_name="Tester")
        details = RegistrationDetails.objects.get(membership__member=child)
        self.assertIsNone(details.requested_team)

    def test_a_product_tagged_for_volunteers_is_rejected_for_a_player_entry(self):
        volunteer_category = ProductCategory.objects.get(club=self.club, registration_kind=ProductCategory.RegistrationKind.VOLUNTEER)
        self.product.category = volunteer_category
        self.product.save(update_fields=["category"])
        data = self.contact_data() | self.formset_management() | self.entry_data(entry_kind=RegistrationDetails.EntryKind.PLAYER)
        data["action"] = "preview"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This option is for Volunteer registrations.")
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

    def test_submitting_sends_a_confirmation_email_with_the_status_link(self):
        data = self.contact_data() | self.formset_management() | self.entry_data()
        data["action"] = "submit"

        self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        batch = RegistrationBatch.objects.get()
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["pat@example.com"])
        status_path = reverse("registration:status", kwargs={"token": batch.status_token})
        self.assertIn(status_path, sent.body)


class RegistrationJerseyNumberTests(RegistrationViewTests):
    """The optional jersey-number step (RegistrationEntryRowForm.
    requested_jersey_number) -- players only, and only once a pool-scoped
    team is chosen. See teams.services.numbers for the availability rules
    themselves; this is just the registration-form integration."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.pool = NumberPool.objects.create(club=cls.club, name="Youth", min_number=1, max_number=20)
        cls.team.pool = cls.pool
        cls.team.save()

    def test_requesting_an_available_number_is_saved(self):
        data = self.contact_data() | self.formset_management() | self.entry_data(requested_jersey_number="7")
        data["action"] = "submit"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        batch = RegistrationBatch.objects.get()
        self.assertRedirects(response, reverse("registration:status", kwargs={"token": batch.status_token}))
        details = RegistrationDetails.objects.get(batch=batch)
        self.assertEqual(details.requested_jersey_number, 7)

    def test_requesting_an_already_taken_number_is_rejected(self):
        other_member = Member.objects.create(first_name="Other", last_name="Kid")
        TeamMembership.objects.create(team=self.team, member=other_member, season=self.season, jersey_number=7)
        data = self.contact_data() | self.formset_management() | self.entry_data(requested_jersey_number="7")
        data["action"] = "preview"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "just taken")
        self.assertFalse(RegistrationBatch.objects.exists())

    def test_a_volunteer_entry_ignores_a_submitted_number(self):
        volunteer_category = ProductCategory.objects.get(club=self.club, registration_kind=ProductCategory.RegistrationKind.VOLUNTEER)
        volunteer_product = Product.objects.create(club=self.club, name="Volunteer", product_type=Product.ProductType.MEMBERSHIP, season=self.season, price=Decimal("0"), category=volunteer_category)
        volunteer_variant = ProductVariant.objects.create(product=volunteer_product, name="Coach", price=Decimal("0"))
        data = self.contact_data() | self.formset_management() | self.entry_data(entry_kind=RegistrationDetails.EntryKind.VOLUNTEER, product_variant=str(volunteer_variant.pk), requested_jersey_number="7")
        data["action"] = "submit"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        batch = RegistrationBatch.objects.get()
        self.assertRedirects(response, reverse("registration:status", kwargs={"token": batch.status_token}))
        details = RegistrationDetails.objects.get(batch=batch)
        self.assertIsNone(details.requested_jersey_number)

    def test_no_team_chosen_ignores_a_submitted_number(self):
        data = self.contact_data() | self.formset_management() | self.entry_data(requested_team="", requested_jersey_number="7")
        data["action"] = "submit"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        batch = RegistrationBatch.objects.get()
        self.assertRedirects(response, reverse("registration:status", kwargs={"token": batch.status_token}))
        details = RegistrationDetails.objects.get(batch=batch)
        self.assertIsNone(details.requested_jersey_number)

    def test_a_team_with_no_pool_ignores_a_submitted_number(self):
        poolless_team = Team.objects.create(club=self.club, name="U8 Boys", short_name="U8B")
        data = self.contact_data() | self.formset_management() | self.entry_data(requested_team=str(poolless_team.pk), requested_jersey_number="7")
        data["action"] = "submit"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        batch = RegistrationBatch.objects.get()
        self.assertRedirects(response, reverse("registration:status", kwargs={"token": batch.status_token}))
        details = RegistrationDetails.objects.get(batch=batch)
        self.assertIsNone(details.requested_jersey_number)

    def test_a_second_pending_request_for_the_same_number_is_rejected(self):
        # Blocked the moment someone registers, before staff has even looked
        # at the first one -- see teams.services.numbers.numbers_taken's own
        # docstring on why a pending request counts too.
        data = self.contact_data() | self.formset_management() | self.entry_data(requested_jersey_number="7")
        data["action"] = "submit"
        self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        second = self.contact_data(contact_email="other@example.com") | self.formset_management() | self.entry_data(first_name="Jamie", requested_jersey_number="7")
        second["action"] = "preview"
        response = self.client.post(self._url(), second, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "just taken")

    def test_two_rows_in_the_same_submission_requesting_the_same_number_are_rejected(self):
        # Neither is on record yet, so only a formset-wide check (not either
        # row's own clean()) can catch this -- see BaseRegistrationEntryFormSet.
        data = self.contact_data() | self.formset_management(2) | self.entry_data(0, requested_jersey_number="7") | self.entry_data(1, first_name="Jamie", requested_jersey_number="7")
        data["action"] = "preview"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already requested this number")
        self.assertFalse(RegistrationBatch.objects.exists())

    def test_team_number_pools_context_lists_available_numbers(self):
        TeamMembership.objects.create(team=self.team, member=Member.objects.create(first_name="Taken", last_name="One"), season=self.season, jersey_number=1)

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        pools = response.context["team_number_pools"]
        self.assertNotIn(1, pools[str(self.team.pk)])
        self.assertIn(2, pools[str(self.team.pk)])

    def test_the_field_does_not_appear_on_the_form_itself(self):
        # It only lives in the price panel now, once calculated -- see
        # register.html's own comment on why.
        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotContains(response, "entries-0-requested_jersey_number")

    def test_the_field_appears_in_the_price_panel_once_calculated(self):
        data = self.contact_data() | self.formset_management() | self.entry_data()
        data["action"] = "preview"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "entries-0-requested_jersey_number")
        self.assertContains(response, "Jersey number (U10B)")

    def test_the_price_panel_field_is_narrowed_to_the_chosen_team(self):
        other_pool = NumberPool.objects.create(club=self.club, name="Senior", min_number=1, max_number=5)
        other_team = Team.objects.create(club=self.club, name="Senior A", short_name="SA", pool=other_pool)
        TeamMembership.objects.create(team=self.team, member=Member.objects.create(first_name="Taken", last_name="One"), season=self.season, jersey_number=1)
        data = self.contact_data() | self.formset_management() | self.entry_data()
        data["action"] = "preview"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        form = response.context["priced_entries"][0][0]
        choices = dict(form.fields["requested_jersey_number"].choices)
        self.assertNotIn("1", choices)  # taken on self.team
        self.assertIn("2", choices)
        # other_team's own pool has numbers 1-5 too, but this entry didn't
        # request that team -- its own choices must stay scoped to self.team.
        self.assertEqual(len(choices), len(range(2, 21)) + 1)  # +1 for the blank option
        self.assertIsNotNone(other_team)

    def test_no_field_at_all_for_a_team_with_no_pool(self):
        poolless_team = Team.objects.create(club=self.club, name="U8 Boys", short_name="U8B")
        data = self.contact_data() | self.formset_management() | self.entry_data(requested_team=str(poolless_team.pk))
        data["action"] = "preview"

        response = self.client.post(self._url(), data, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotContains(response, "entries-0-requested_jersey_number")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class RegistrationStatusViewTests(TestCase):
    """registration:status -- the unauthenticated, token-gated link the
    confirmation email hands out (RegistrationStatusView)."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.season = make_season(cls.club)
        cls.child = Member.objects.create(first_name="Timmy", last_name="Tester", date_of_birth=datetime.date(2016, 5, 1))
        cls.membership = ClubMembership.objects.create(club=cls.club, member=cls.child, season=cls.season, status=ClubMembership.StatusChoices.PENDING, fee_amount=Decimal("80.00"))
        cls.batch = RegistrationBatch.objects.create(club=cls.club, season=cls.season, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com")
        RegistrationDetails.objects.create(membership=cls.membership, batch=cls.batch, entry_kind=RegistrationDetails.EntryKind.PLAYER, product_variant=None, price=Decimal("80.00"))
        cls.requirement = OnboardingRequirement.objects.create(club=cls.club, name="Medical certificate", requires_document=True)

    def _url(self, token=None):
        return reverse("registration:status", kwargs={"token": token or self.batch.status_token})

    def _confirm(self, batch=None):
        # Sets the confirmation state directly rather than going through the
        # real management review-and-confirm flow (a different screen this
        # test class isn't about) -- invoice_sent_at is the one flag every
        # money-related block on this page actually reads.
        batch = batch or self.batch
        batch.invoice_number = "REG-9999-00001"
        batch.invoice_sent_at = timezone.now()
        batch.invoice_due_date = datetime.date.today() + datetime.timedelta(days=14)
        batch.save()
        return batch

    def test_an_unknown_token_404s(self):
        response = self.client.get(self._url(token="not-a-real-token"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_shows_the_members_status_and_open_checklist(self):
        # Onboarding -- unaffected by whether the invoice has been confirmed.
        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Timmy Tester")
        self.assertContains(response, "Medical certificate")

    def test_uploading_a_document_marks_the_requirement_complete(self):
        upload = SimpleUploadedFile("cert.pdf", b"file-bytes", content_type="application/pdf")

        response = self.client.post(self._url(), {"membership": str(self.membership.pk), "requirement": str(self.requirement.pk), "document": upload}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, self._url())
        status = MemberRequirementStatus.objects.get(membership=self.membership, requirement=self.requirement)
        self.assertTrue(status.is_complete)
        self.assertIsNone(status.completed_by)
        self.assertTrue(status.document.name)

    def test_a_membership_outside_this_batch_cannot_be_targeted(self):
        other_child = Member.objects.create(first_name="Alex", last_name="Outsider")
        other_membership = ClubMembership.objects.create(club=self.club, member=other_child, season=self.season, status=ClubMembership.StatusChoices.PENDING)
        upload = SimpleUploadedFile("cert.pdf", b"file-bytes", content_type="application/pdf")

        response = self.client.post(self._url(), {"membership": str(other_membership.pk), "requirement": str(self.requirement.pk), "document": upload}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(MemberRequirementStatus.objects.filter(membership=other_membership).exists())

    def test_the_download_invoice_link_points_at_the_batch_not_a_membership(self):
        self._confirm()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, reverse("registration:invoice", kwargs={"token": self.batch.status_token}))

    def test_shows_the_early_payment_offer_while_still_open(self):
        self._confirm()
        self.membership.early_payment_deadline = datetime.date.today() + datetime.timedelta(days=5)
        self.membership.early_payment_discount = Decimal("10.00")
        self.membership.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "70.00")

    def test_hides_the_early_payment_offer_once_the_deadline_has_passed(self):
        self._confirm()
        self.membership.early_payment_deadline = datetime.date.today() - datetime.timedelta(days=1)
        self.membership.early_payment_discount = Decimal("10.00")
        self.membership.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertIsNone(response.context["early_payment"])

    def test_shows_payment_instructions_when_set(self):
        self._confirm()
        self.club.payment_instructions = "Bank transfer to BE00 0000 0000 0000"
        self.club.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "How to pay")
        self.assertContains(response, "BE00 0000 0000 0000")

    def test_no_payment_instructions_block_when_blank(self):
        self._confirm()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotContains(response, "How to pay")

    def test_before_confirmation_nothing_financial_is_shown(self):
        # Not confirmed -- no balance line, no early-payment offer, no
        # Download invoice button, no How to pay card, even though the fee/
        # payment_instructions data all already exists underneath.
        self.club.payment_instructions = "Bank transfer to BE00 0000 0000 0000"
        self.club.save()
        self.membership.early_payment_deadline = datetime.date.today() + datetime.timedelta(days=5)
        self.membership.early_payment_discount = Decimal("10.00")
        self.membership.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertFalse(response.context["invoice_ready"])
        self.assertIsNone(response.context["membership_rows"][0]["balance"])
        self.assertIsNone(response.context["early_payment"])
        self.assertNotContains(response, "still owed")
        self.assertNotContains(response, "No balance owed")
        self.assertNotContains(response, "Download invoice")
        self.assertNotContains(response, "How to pay")
        self.assertNotContains(response, "BE00 0000 0000 0000")

    def test_after_confirmation_the_balance_is_shown(self):
        self._confirm()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertTrue(response.context["invoice_ready"])
        self.assertContains(response, "80.00")
        self.assertContains(response, "still owed")
        self.assertContains(response, "Cancel registration")

    def test_cancel_registration_disappears_once_the_fee_is_paid(self):
        # Cancelling something already settled doesn't make sense any more.
        self._confirm()
        self.membership.amount_paid = Decimal("80.00")
        self.membership.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertTrue(response.context["membership_rows"][0]["paid"])
        self.assertNotContains(response, "Cancel registration")

    def test_cancel_registration_still_shows_before_confirmation(self):
        # Nothing's been paid yet at this point -- row.paid is only ever
        # meaningful once invoice_ready, see get_membership_rows.
        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertFalse(response.context["membership_rows"][0]["paid"])
        self.assertContains(response, "Cancel registration")

    def test_a_credit_shows_as_its_own_real_number_not_no_balance_owed(self):
        # remaining_balance floors at 0 -- net_balance (what this page reads
        # instead) doesn't, so a credit (an overpayment here, or a negative
        # per-line price set on the Registrations review screen) shows as an
        # actual number rather than folding into the same "No balance owed"
        # text a genuinely-settled member gets.
        self._confirm()
        self.membership.amount_paid = Decimal("100.00")
        self.membership.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.context["membership_rows"][0]["balance"], Decimal("-20.00"))
        self.assertEqual(response.context["membership_rows"][0]["credit"], Decimal("20.00"))
        self.assertContains(response, "20.00")
        self.assertContains(response, "credit")
        self.assertNotContains(response, "No balance owed")

    def test_a_genuinely_settled_balance_still_reads_no_balance_owed(self):
        self._confirm()
        self.membership.amount_paid = Decimal("80.00")
        self.membership.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.context["membership_rows"][0]["balance"], Decimal("0.00"))
        self.assertIsNone(response.context["membership_rows"][0]["credit"])
        self.assertContains(response, "No balance owed")


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class RegistrationCancelViewTests(TestCase):
    """registration:cancel -- withdrawing a registration from the status
    page, the family-facing half of club.services.cancellation."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.season = make_season(cls.club)
        cls.child = Member.objects.create(first_name="Timmy", last_name="Tester")
        cls.membership = ClubMembership.objects.create(club=cls.club, member=cls.child, season=cls.season, status=ClubMembership.StatusChoices.PENDING)
        cls.batch = RegistrationBatch.objects.create(club=cls.club, season=cls.season, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com")
        RegistrationDetails.objects.create(membership=cls.membership, batch=cls.batch, entry_kind=RegistrationDetails.EntryKind.PLAYER)

    def _url(self, membership=None):
        return reverse("registration:cancel", kwargs={"token": self.batch.status_token, "membership_pk": (membership or self.membership).pk})

    def test_cancelling_a_brand_new_member_deletes_them(self):
        response = self.client.post(self._url(), {}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertRedirects(response, reverse("registration:status", kwargs={"token": self.batch.status_token}))
        self.assertFalse(Member.objects.filter(pk=self.child.pk).exists())

    def test_cancelling_a_returning_member_soft_cancels(self):
        previous_season = make_season(self.club, start=datetime.date(2020, 1, 1), end=datetime.date(2020, 12, 31))
        ClubMembership.objects.create(club=self.club, member=self.child, season=previous_season, status=ClubMembership.StatusChoices.LAPSED)

        self.client.post(self._url(), {}, HTTP_HOST="ajax-united.rosterchief.app")

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, ClubMembership.StatusChoices.CANCELLED)

    def test_a_membership_outside_this_batch_cannot_be_cancelled(self):
        other_child = Member.objects.create(first_name="Alex", last_name="Outsider")
        other_membership = ClubMembership.objects.create(club=self.club, member=other_child, season=self.season, status=ClubMembership.StatusChoices.PENDING)

        response = self.client.post(self._url(membership=other_membership), {}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Member.objects.filter(pk=other_child.pk).exists())

    def test_an_unknown_token_404s(self):
        response = self.client.post(reverse("registration:cancel", kwargs={"token": "not-a-real-token", "membership_pk": self.membership.pk}), {}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)


@override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"])
class RegistrationInvoiceViewTests(TestCase):
    """registration:invoice -- one PDF covering every person in the batch,
    not one per membership (RegistrationInvoiceView)."""

    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        cls.season = make_season(cls.club)
        cls.child = Member.objects.create(first_name="Timmy", last_name="Tester", date_of_birth=datetime.date(2016, 5, 1))
        cls.sibling = Member.objects.create(first_name="Jamie", last_name="Tester", date_of_birth=datetime.date(2018, 3, 1))
        cls.membership = ClubMembership.objects.create(club=cls.club, member=cls.child, season=cls.season, status=ClubMembership.StatusChoices.PENDING, fee_amount=Decimal("80.00"))
        cls.sibling_membership = ClubMembership.objects.create(club=cls.club, member=cls.sibling, season=cls.season, status=ClubMembership.StatusChoices.PENDING, fee_amount=Decimal("80.00"))
        cls.batch = RegistrationBatch.objects.create(
            club=cls.club,
            season=cls.season,
            contact_first_name="Pat",
            contact_last_name="Parent",
            contact_email="pat@example.com",
            subtotal=Decimal("160.00"),
            total=Decimal("160.00"),
            # Confirmed by default -- every test in this class is about the
            # PDF-rendering behaviour once staff has already reviewed and
            # sent it, not the confirmation gate itself (see the dedicated
            # tests for that below).
            invoice_number="REG-9999-00001",
            invoice_sent_at=timezone.now(),
            invoice_due_date=datetime.date.today() + datetime.timedelta(days=14),
        )
        RegistrationDetails.objects.create(membership=cls.membership, batch=cls.batch, entry_kind=RegistrationDetails.EntryKind.PLAYER, price=Decimal("80.00"))
        RegistrationDetails.objects.create(membership=cls.sibling_membership, batch=cls.batch, entry_kind=RegistrationDetails.EntryKind.PLAYER, price=Decimal("80.00"))

    def _url(self, token=None):
        return reverse("registration:invoice", kwargs={"token": token or self.batch.status_token})

    def test_an_unknown_token_404s(self):
        response = self.client.get(self._url(token="not-a-real-token"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_404s_before_the_invoice_is_confirmed(self):
        self.batch.invoice_sent_at = None
        self.batch.save()

        response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_downloads_as_a_single_pdf_covering_every_entry(self):
        with mock.patch("registration.views.batch_invoice_pdf", return_value=b"%PDF-fake") as renderer:
            response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response.content, b"%PDF-fake")
        renderer.assert_called_once_with(self.batch)

    def test_the_rendered_pdf_lists_every_entry_and_the_batch_total(self):
        # No mock here -- exercises the actual template against real data
        # (batch_invoice_pdf's own render_to_string call), stopping short of
        # the WeasyPrint step by patching just that.
        with mock.patch("registration.services.invoicing.render_pdf", side_effect=lambda html: html.encode()) as renderer:
            response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        renderer.assert_called_once()
        html = response.content.decode()
        self.assertIn("Timmy Tester", html)
        self.assertIn("Jamie Tester", html)
        self.assertIn("160.00", html)

    def test_a_cancelled_entrys_membership_is_left_off_the_invoice(self):
        self.sibling_membership.status = ClubMembership.StatusChoices.CANCELLED
        self.sibling_membership.save()

        with mock.patch("registration.services.invoicing.render_pdf", side_effect=lambda html: html.encode()):
            response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        html = response.content.decode()
        self.assertIn("Timmy Tester", html)
        self.assertNotIn("Jamie Tester", html)
        # Recomputed from what's left, not the batch's own stored total (set
        # once, at submission, over both entries) -- only Timmy's 80.00 is
        # still actually owed.
        self.assertNotIn("160.00", html)
        self.assertIn("80.00", html)

    def test_a_missing_pdf_library_is_reported_rather_than_a_500(self):
        with mock.patch("registration.views.batch_invoice_pdf", side_effect=RegistrationInvoicePDFError("PDF rendering needs the native pango/cairo libraries.")):
            response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")
        response = self.client.get(response.url, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "pango")

    def test_includes_payment_instructions_when_set(self):
        self.club.payment_instructions = "Bank transfer to BE00 0000 0000 0000"
        self.club.save()

        with mock.patch("registration.services.invoicing.render_pdf", side_effect=lambda html: html.encode()):
            response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertIn("BE00 0000 0000 0000", response.content.decode())

    def test_no_payment_instructions_section_when_blank(self):
        with mock.patch("registration.services.invoicing.render_pdf", side_effect=lambda html: html.encode()):
            response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotIn("How to pay", response.content.decode())

    def test_shows_the_early_payment_offer_while_still_open(self):
        # One combined figure for the whole registration, not this one
        # membership's own line -- batch.total (both this and the sibling
        # membership's 80.00) minus this one's own 10.00 discount, since
        # only this membership has a live offer.
        self.membership.early_payment_deadline = datetime.date.today() + datetime.timedelta(days=5)
        self.membership.early_payment_discount = Decimal("10.00")
        self.membership.save()

        with mock.patch("registration.services.invoicing.render_pdf", side_effect=lambda html: html.encode()):
            response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        html = response.content.decode()
        self.assertIn("Pay early and save", html)
        self.assertIn("150.00", html)
        self.assertIn("10.00", html)

    def test_no_early_payment_section_once_the_deadline_has_passed(self):
        self.membership.early_payment_deadline = datetime.date.today() - datetime.timedelta(days=1)
        self.membership.early_payment_discount = Decimal("10.00")
        self.membership.save()

        with mock.patch("registration.services.invoicing.render_pdf", side_effect=lambda html: html.encode()):
            response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotIn("Pay early and save", response.content.decode())

    def test_no_early_payment_section_when_nobody_has_an_offer(self):
        with mock.patch("registration.services.invoicing.render_pdf", side_effect=lambda html: html.encode()):
            response = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotIn("Pay early and save", response.content.decode())

    def test_regenerates_on_every_request_not_cached(self):
        # batch_invoice_pdf is no longer disk-cached (see its own module
        # docstring) -- early_payment's own liveness is time-based, so a
        # second render after the club's payment_instructions changed must
        # reflect that change, not a stale first render.
        with mock.patch("registration.services.invoicing.render_pdf", side_effect=lambda html: html.encode()):
            first = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")
        self.assertNotIn("How to pay", first.content.decode())

        self.club.payment_instructions = "Bank transfer to BE00 0000 0000 0000"
        self.club.save()

        with mock.patch("registration.services.invoicing.render_pdf", side_effect=lambda html: html.encode()):
            second = self.client.get(self._url(), HTTP_HOST="ajax-united.rosterchief.app")
        self.assertIn("BE00 0000 0000 0000", second.content.decode())
