"""The member web app's Register screen as iOS has it: a list of added registrations, an "Add a registration" editor sheet
(Person or "Someone new", Role, Option, Team preference, Jersey number), then "See the price" and "Send registration".

The draft is carried as hidden formset inputs, so the price and the records created must be exactly what the old
all-rows-on-one-page formset flow produced for the same choices."""

import datetime
import re
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from authentication.models import User
from club.models import ClubMembership, Season
from members.models import Family, FamilyMembership, Member
from registration.models import RegistrationBatch, RegistrationDetails
from rosterchief.test_support import make_club
from shop.models import Product, ProductVariant
from teams.models import NumberPool, Team, TeamMembership

HOST = "ajax-united.rosterchief.app"
SETTINGS = {"ROSTERCHIEF_BASE_DOMAIN": "rosterchief.app", "ALLOWED_HOSTS": ["rosterchief.app", HOST, "testserver"]}
HIDDEN = re.compile(r'<input type="hidden" name="(entries-[^"]+)" value="([^"]*)"')


@override_settings(**SETTINGS)
class RegisterFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.club = make_club()
        today = timezone.localdate()
        cls.season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
        last_season = Season.objects.create(club=cls.club, start_date=today - datetime.timedelta(days=395), end_date=today - datetime.timedelta(days=31))
        cls.user = User.objects.create_user(email="parent@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(first_name="Lars", last_name="Bakker", email="lars@example.com", user=cls.user)
        ClubMembership.objects.create(club=cls.club, member=cls.member, season=cls.season, status=ClubMembership.StatusChoices.ACTIVE)
        cls.family = Family.objects.create(club=cls.club, name="Bakker")
        FamilyMembership.objects.create(family=cls.family, member=cls.member, role=FamilyMembership.FamilyRole.PARENT)
        cls.child = Member.objects.create(first_name="Noor", last_name="Bakker")
        FamilyMembership.objects.create(family=cls.family, member=cls.child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=cls.club, member=cls.child, season=last_season, status=ClubMembership.StatusChoices.LAPSED)
        pool = NumberPool.objects.create(club=cls.club, name="Youth", min_number=1, max_number=20)
        cls.team = Team.objects.create(club=cls.club, name="U10 Boys", short_name="U10B", pool=pool)
        TeamMembership.objects.create(team=cls.team, member=cls.child, season=last_season, jersey_number=7)
        cls.product = Product.objects.create(club=cls.club, name="Player Registration", product_type=Product.ProductType.MEMBERSHIP, season=cls.season, price=Decimal("100.00"))
        cls.u10 = ProductVariant.objects.create(product=cls.product, name="U10", price=Decimal("80.00"))
        cls.u12 = ProductVariant.objects.create(product=cls.product, name="U12", price=Decimal("90.00"))
        cls.url = reverse("mobile:reregister")

    def setUp(self):
        self.client.force_login(self.user)

    def post(self, data, **extra):
        return self.client.post(self.url, data, HTTP_HOST=HOST, **extra)

    @staticmethod
    def state(response):
        """The hidden entries-* inputs of a rendered page: exactly what the browser would post back."""
        return dict(HIDDEN.findall(response.content.decode()))

    def save(self, state, *, person="", index="", **fields):
        """Post the editor ("Save") on top of ``state``; ``person`` is a Member pk or "" for someone new."""
        data = {**state, "action": "save", "season": str(self.season.pk), "editor_index": index, "editor-existing_member": person, "editor-entry_kind": "player"}
        data.update({f"editor-{name}": value for name, value in fields.items()})
        return self.post(data)

    def empty_state(self):
        return {"entries-TOTAL_FORMS": "0", "entries-INITIAL_FORMS": "0", "entries-MIN_NUM_FORMS": "0", "entries-MAX_NUM_FORMS": "1000"}

    # --- the list and the editor sheet ---------------------------------------------

    def test_the_screen_starts_with_an_empty_list_an_add_button_and_a_closed_sheet(self):
        response = self.client.get(self.url, HTTP_HOST=HOST)

        self.assertContains(response, "No registrations yet.")
        self.assertContains(response, "Add a registration")
        self.assertContains(response, "See the price")
        self.assertContains(response, 'id="registration-editor"')
        self.assertNotContains(response, "data-autoopen")

    def test_add_over_htmx_returns_just_the_editor_and_without_htmx_the_page_with_the_sheet_open(self):
        data = {**self.empty_state(), "action": "add", "season": str(self.season.pk)}

        fragment = self.post(data, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="registration-editor-body")
        page = self.post(data)

        self.assertContains(fragment, 'name="editor-existing_member"')
        self.assertNotContains(fragment, "<html")
        self.assertContains(page, "data-autoopen")
        self.assertContains(page, 'name="editor-existing_member"')
        self.assertContains(page, "<html")

    def test_the_editor_offers_the_managed_people_someone_new_and_the_roles(self):
        response = self.post({**self.empty_state(), "action": "add", "season": str(self.season.pk)})

        self.assertContains(response, "Lars Bakker")
        self.assertContains(response, "Noor Bakker")
        self.assertContains(response, "Someone new")
        self.assertContains(response, "Player")
        self.assertContains(response, "Volunteer")
        self.assertContains(response, "The club makes the final choice.")
        self.assertContains(response, "No preference")

    def test_saving_a_known_person_adds_a_card_and_hidden_state(self):
        response = self.save(self.empty_state(), person=str(self.member.pk), product_variant=str(self.u10.pk), requested_team=str(self.team.pk))

        self.assertEqual(response.status_code, 200)
        card = response.context["entry_cards"][0]
        self.assertEqual((card["name"], card["role"], card["option"], card["team"]), ("Lars Bakker", "Player", "Player Registration · U10", "U10 Boys"))
        state = self.state(response)
        self.assertEqual(state["entries-TOTAL_FORMS"], "1")
        self.assertEqual(state["entries-0-existing_member"], str(self.member.pk))
        self.assertEqual(state["entries-0-product_variant"], str(self.u10.pk))
        self.assertFalse(RegistrationBatch.objects.exists())

    def test_saving_someone_new_needs_a_name_and_keeps_the_sheet_open_with_the_errors(self):
        response = self.save(self.empty_state(), product_variant=str(self.u10.pk))

        self.assertContains(response, "data-autoopen")
        self.assertContains(response, "Enter a first name.")
        self.assertContains(response, "Enter a last name.")
        self.assertEqual(response.context["entry_cards"], [])

    def test_saving_without_an_option_is_refused(self):
        response = self.save(self.empty_state(), person=str(self.member.pk))

        self.assertContains(response, "Choose what this person is registering for.")
        self.assertEqual(response.context["entry_cards"], [])

    def test_a_volunteer_cannot_pick_a_player_only_option_and_the_options_follow_the_role(self):
        from shop.models import ProductCategory

        category = ProductCategory.objects.create(club=self.club, name="Players", registration_kind=ProductCategory.RegistrationKind.PLAYER)
        self.product.category = category
        self.product.save()
        refresh = self.post({**self.empty_state(), "action": "refresh", "season": str(self.season.pk), "editor-existing_member": str(self.member.pk), "editor-entry_kind": "volunteer"})
        self.assertNotContains(refresh, "Player Registration")

        refused = self.save(self.empty_state(), person=str(self.member.pk), entry_kind="volunteer", product_variant=str(self.u10.pk))
        self.assertEqual(refused.context["entry_cards"], [])

    def test_choosing_a_team_with_a_number_pool_offers_the_jersey_numbers_with_keep(self):
        data = {**self.empty_state(), "action": "refresh", "season": str(self.season.pk), "editor-existing_member": str(self.child.pk), "editor-entry_kind": "player", "editor-requested_team": str(self.team.pk)}

        response = self.post(data, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="registration-editor-body")

        self.assertContains(response, "Keep #7")
        self.assertContains(response, 'name="editor-requested_jersey_number"')
        without_team = self.post({**data, "editor-requested_team": ""}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="registration-editor-body")
        self.assertNotContains(without_team, 'name="editor-requested_jersey_number"')

    def test_edit_prefills_the_sheet_and_saving_replaces_that_entry(self):
        state = self.state(self.save(self.empty_state(), person=str(self.member.pk), product_variant=str(self.u10.pk)))

        opened = self.post({**state, "action": "change:0", "season": str(self.season.pk)})
        self.assertContains(opened, "Edit registration")
        self.assertContains(opened, f'<option value="{self.u10.pk}" selected>')

        saved = self.save(state, person=str(self.member.pk), index="0", product_variant=str(self.u12.pk))
        self.assertEqual(len(saved.context["entry_cards"]), 1)
        self.assertEqual(self.state(saved)["entries-0-product_variant"], str(self.u12.pk))

    def test_remove_drops_just_that_entry_and_reindexes(self):
        state = self.state(self.save(self.empty_state(), person=str(self.member.pk), product_variant=str(self.u10.pk)))
        state = self.state(self.save(state, person=str(self.child.pk), product_variant=str(self.u12.pk)))
        self.assertEqual(state["entries-TOTAL_FORMS"], "2")

        response = self.post({**state, "action": "remove:0", "season": str(self.season.pk)})

        state = self.state(response)
        self.assertEqual(state["entries-TOTAL_FORMS"], "1")
        self.assertEqual(state["entries-0-existing_member"], str(self.child.pk))

    def test_cancel_leaves_the_list_untouched(self):
        state = self.state(self.save(self.empty_state(), person=str(self.member.pk), product_variant=str(self.u10.pk)))

        response = self.post({**state, "action": "cancel", "season": str(self.season.pk), "editor-existing_member": str(self.child.pk)})

        self.assertEqual(len(response.context["entry_cards"]), 1)
        self.assertNotContains(response, "data-autoopen")

    def test_the_list_is_capped_at_ten_registrations(self):
        state = self.empty_state()
        for _ in range(10):
            state = self.state(self.save(state, person=str(self.member.pk), product_variant=str(self.u10.pk)))
        self.assertEqual(state["entries-TOTAL_FORMS"], "10")

        response = self.post({**state, "action": "add", "season": str(self.season.pk)})

        self.assertContains(response, "You can submit at most 10 registrations at a time.")
        self.assertNotContains(response, "data-autoopen")
        self.assertNotContains(response, "data-add-registration")

    # --- price and send: unchanged from the all-rows-at-once flow --------------------

    def build_list(self):
        """Lars (U10, U10 Boys), Noor (U12, keeps #7) and a brand-new Baby (U10) through the editor."""
        state = self.state(self.save(self.empty_state(), person=str(self.member.pk), product_variant=str(self.u10.pk), requested_team=str(self.team.pk)))
        state = self.state(self.save(state, person=str(self.child.pk), product_variant=str(self.u12.pk), requested_team=str(self.team.pk), requested_jersey_number="7"))
        response = self.save(state, first_name="Baby", last_name="Bakker", date_of_birth="2024-05-06", product_variant=str(self.u10.pk))
        return self.state(response)

    def legacy_payload(self):
        """The same three registrations posted the way the old one-page formset did."""
        data = {"entries-TOTAL_FORMS": "4", "entries-INITIAL_FORMS": "0", "entries-MIN_NUM_FORMS": "0", "entries-MAX_NUM_FORMS": "1000", "season": str(self.season.pk)}
        data.update({"entries-0-existing_member": str(self.member.pk), "entries-0-is_contact": "on", "entries-0-product_variant": str(self.u10.pk), "entries-0-requested_team": str(self.team.pk)})
        data.update({"entries-1-existing_member": str(self.child.pk), "entries-1-product_variant": str(self.u12.pk), "entries-1-requested_team": str(self.team.pk), "entries-1-requested_jersey_number": "7"})
        data.update({"entries-3-first_name": "Baby", "entries-3-last_name": "Bakker", "entries-3-date_of_birth": "2024-05-06", "entries-3-product_variant": str(self.u10.pk)})
        return data

    def test_the_list_state_is_the_formset_the_old_flow_posted(self):
        state = self.build_list()

        self.assertEqual(state["entries-TOTAL_FORMS"], "3")
        self.assertEqual(state["entries-1-requested_jersey_number"], "7")
        self.assertEqual(state["entries-2-first_name"], "Baby")
        self.assertEqual(state["entries-2-date_of_birth"], "2024-05-06")
        self.assertNotIn("entries-2-existing_member", state)

    def test_the_price_is_the_same_as_for_the_old_flow(self):
        new = self.post({**self.build_list(), "season": str(self.season.pk), "action": "preview"})
        old = self.post({**self.legacy_payload(), "action": "preview"})

        self.assertEqual(new.context["priced_total"], old.context["priced_total"])
        self.assertEqual(new.context["priced_early_total"], old.context["priced_early_total"])
        self.assertEqual([row["price"] for _f, _e, row in new.context["priced_entries"]], [row["price"] for _f, _e, row in old.context["priced_entries"]])
        self.assertEqual(len(new.context["priced_entries"]), 3)
        self.assertContains(new, "Send registration")
        self.assertContains(new, "Keep #7")
        self.assertFalse(RegistrationBatch.objects.exists())

    def test_back_from_the_price_returns_to_the_list_with_the_same_entries(self):
        priced = self.post({**self.build_list(), "season": str(self.season.pk), "action": "preview"})
        # The receipt's own jersey picker posts the number back.
        state = {**self.state(priced), "entries-1-requested_jersey_number": "7"}

        back = self.post({**state, "season": str(self.season.pk), "action": "edit"})

        self.assertIsNone(back.context["priced_entries"])
        self.assertEqual([card["name"] for card in back.context["entry_cards"]], ["Lars Bakker", "Noor Bakker", "Baby Bakker"])
        self.assertContains(back, "Edit")

    def test_sending_creates_the_same_records_as_the_old_flow(self):
        priced = self.post({**self.build_list(), "season": str(self.season.pk), "action": "preview"})
        # What the browser posts from the price screen: the hidden list plus the receipt's jersey pickers.
        data = {**self.state(priced), "season": str(self.season.pk), "action": "submit", "entries-1-requested_jersey_number": "7"}

        response = self.post(data)

        self.assertRedirects(response, reverse("mobile:me"), fetch_redirect_response=False)
        batch = RegistrationBatch.objects.get()
        self.assertEqual(batch.entries.count(), 3)
        rows = sorted(RegistrationDetails.objects.values_list("membership__member__first_name", "product_variant__name", "requested_team__name", "requested_jersey_number", "price"))
        self.assertEqual(rows, [("Baby", "U10", None, None, Decimal("80.00")), ("Lars", "U10", "U10 Boys", None, Decimal("80.00")), ("Noor", "U12", "U10 Boys", 7, Decimal("90.00"))])
        self.assertEqual(Member.objects.filter(first_name="Noor").count(), 1)
        baby = Member.objects.get(first_name="Baby")
        self.assertEqual(baby.date_of_birth, datetime.date(2024, 5, 6))
        self.assertEqual(baby.family_memberships.get().family, self.family)
        self.assertEqual(ClubMembership.objects.get(member=self.child, season=self.season).status, ClubMembership.StatusChoices.PENDING)
        self.assertEqual(batch.submitted_by_user, self.user)

    def test_the_old_one_page_post_still_works_end_to_end(self):
        response = self.post({**self.legacy_payload(), "action": "submit"})

        self.assertRedirects(response, reverse("mobile:me"), fetch_redirect_response=False)
        self.assertEqual(RegistrationDetails.objects.count(), 3)

    def test_a_list_with_an_invalid_entry_shows_the_error_on_that_card(self):
        data = {**self.empty_state(), "entries-TOTAL_FORMS": "1", "entries-0-existing_member": str(self.member.pk), "entries-0-is_contact": "on", "season": str(self.season.pk), "action": "preview"}

        response = self.post(data)

        self.assertContains(response, "Choose what this person is registering for.")
        self.assertIsNone(response.context["priced_entries"])

    def test_an_empty_list_cannot_be_priced(self):
        response = self.post({**self.empty_state(), "season": str(self.season.pk), "action": "preview"})

        self.assertContains(response, "Register at least one person.")

    def test_a_stranger_cannot_be_registered_through_the_editor(self):
        outsider = Member.objects.create(first_name="Eve", last_name="Other")

        response = self.save(self.empty_state(), person=str(outsider.pk), product_variant=str(self.u10.pk))

        self.assertEqual(response.context["entry_cards"], [])
        self.assertFalse(RegistrationBatch.objects.exists())

    def test_the_desktop_page_uses_the_same_list_and_sheet(self):
        desktop = {"HTTP_USER_AGENT": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Chrome/120 Safari/537.36"}

        page = self.client.get(self.url, HTTP_HOST=HOST, **desktop)
        sheet = self.client.post(self.url, {**self.empty_state(), "action": "add", "season": str(self.season.pk)}, HTTP_HOST=HOST, **desktop)

        self.assertContains(page, "Add a registration")
        self.assertContains(page, "See the price")
        self.assertContains(page, 'id="registration-editor"')
        self.assertContains(sheet, "data-autoopen")
        self.assertContains(sheet, "Someone new")
