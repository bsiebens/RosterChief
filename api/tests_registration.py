"""Public registration: the join page, pricing, submitting, the billing review gate, the status page, linking and the staff API."""

import datetime
import json
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.utils import timezone

from controlpanel.tests import login_session
from members.models import ClubMembership, Family, FamilyMembership, Member
from members.services import onboarding
from news.tests import NewsClubMixin
from registration.models import RegistrationBatch
from registration.services import config, pricing
from shop.models import DiscountType, Product, ProductCategory, ProductRegistrantDiscountTier, ProductVariant
from teams.models import NumberPool, NumberReservation, Position, TeamMembership

BASE = "/api/v1"
TODAY = datetime.date.today()


class RegistrationMixin(NewsClubMixin):
    def setUp(self):
        super().setUp()
        cache.clear()
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)
        self.product = Product.objects.create(name="Player membership", price=Decimal("100.00"), product_type=Product.ProductType.MEMBERSHIP, season=self.season)
        self.variant = ProductVariant.objects.create(product=self.product, name="Player membership")
        self.free = Product.objects.create(name="Volunteer", price=Decimal("0.00"), product_type=Product.ProductType.MEMBERSHIP, season=self.season)
        self.free_variant = ProductVariant.objects.create(product=self.free, name="Volunteer")
        self.position = Position.objects.create(name="Forward", short_name="F")
        config.update_settings(self.owner, is_open=True, payment_instructions="IBAN BE00 0000")

    def public(self, method, path, data=None, **extra):
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + "/public/registration" + path, **body, HTTP_HOST=self.host, **extra)

    def staff(self, method, path, data=None, user=None):
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(user or self.owner)}
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def entry(self, first="Kid", last="Sharks", **extra):
        return {"who": "child", "first_name": first, "last_name": last, "date_of_birth": "2015-05-05", "variant_id": str(self.variant.pk), **extra}

    def contact(self, **extra):
        return {"first_name": "Pat", "last_name": "Sharks", "email": "pat@join.test", **extra}

    def register(self, entries=None, contact=None, **extra):
        with self.captureOnCommitCallbacks(execute=True):
            return self.public("post", "", {"contact": contact or self.contact(), "entries": entries if entries is not None else [self.entry()], **extra})

    def batch(self, **kw) -> RegistrationBatch:
        r = self.register(**kw)
        self.assertEqual(r.status_code, 201, r.content)
        return RegistrationBatch.objects.get(status_token=r.json()["token"])


class PricingTests(RegistrationMixin, TestCase):
    def prices(self, n, variant=None):
        return pricing.price_entries([variant or self.variant] * n, today=TODAY)

    def test_plain_price_and_season(self):
        p = self.prices(1)
        self.assertEqual((p.total, p.season.pk), (Decimal("100.00"), self.season.pk))

    def test_per_person_percentage_tier(self):
        ProductRegistrantDiscountTier.objects.create(product=self.product, min_registrants=2, discount_type=DiscountType.PERCENTAGE, discount_amount=Decimal("10"))
        self.assertEqual(self.prices(1).total, Decimal("100.00"))
        two = self.prices(2)
        self.assertEqual([ln.price for ln in two.lines], [Decimal("90.00"), Decimal("90.00")])

    def test_highest_tier_wins_and_fixed_per_person(self):
        for n, amount in ((2, 5), (3, 15)):
            ProductRegistrantDiscountTier.objects.create(product=self.product, min_registrants=n, discount_type=DiscountType.FIXED_AMOUNT, discount_amount=Decimal(amount))
        self.assertEqual(self.prices(2).total, Decimal("190.00"))
        self.assertEqual(self.prices(3).total, Decimal("255.00"))  # 3 x (100 - 15)

    def test_fixed_per_order_is_taken_off_once(self):
        self.product.registrant_discount_scope = Product.RegistrantDiscountScope.PER_ORDER
        self.product.save()
        ProductRegistrantDiscountTier.objects.create(product=self.product, min_registrants=2, discount_type=DiscountType.FIXED_AMOUNT, discount_amount=Decimal("25.00"))
        three = self.prices(3)
        self.assertEqual(three.total, Decimal("275.00"))
        self.assertEqual(sum(ln.discount for ln in three.lines), Decimal("25.00"))  # the rounding cents are not lost

    def test_early_bird_only_before_the_deadline(self):
        self.product.early_bird_discount_type, self.product.early_bird_discount_amount, self.product.early_bird_deadline = DiscountType.PERCENTAGE, Decimal("20"), TODAY + datetime.timedelta(days=5)
        self.product.save()
        p = self.prices(1)
        self.assertEqual((p.total, p.lines[0].early_bird_discount, p.lines[0].early_bird_deadline), (Decimal("100.00"), Decimal("20.00"), self.product.early_bird_deadline))
        self.product.early_bird_deadline = TODAY - datetime.timedelta(days=1)
        self.product.save()
        self.assertEqual(self.prices(1).lines[0].early_bird_discount, Decimal("0.00"))

    def test_products_of_different_seasons_do_not_mix(self):
        other = Product.objects.create(
            name="Next", price=Decimal("50"), product_type=Product.ProductType.MEMBERSHIP, season=self.season.__class__.objects.create(start_date=TODAY + datetime.timedelta(days=400), end_date=TODAY + datetime.timedelta(days=700))
        )
        v = ProductVariant.objects.create(product=other, name="Next")
        from members.services.errors import Invalid

        with self.assertRaises(Invalid):
            pricing.price_entries([self.variant, v])


class JoinPageTests(RegistrationMixin, TestCase):
    def test_closed_until_opened(self):
        config.update_settings(self.owner, is_open=False)
        self.assertFalse(self.public("get", "").json()["is_open"])
        r = self.register()
        self.assertEqual((r.status_code, r.json()["code"]), (422, "closed"))
        self.assertEqual(RegistrationBatch.objects.count(), 0)

    def test_page_lists_only_offerable_membership_products(self):
        Product.objects.create(name="Hidden", price=1, product_type=Product.ProductType.MEMBERSHIP, season=self.season, is_public=False)
        Product.objects.create(name="Shirt", price=1, product_type=Product.ProductType.MERCHANDISE)
        body = self.public("get", "").json()
        self.assertTrue(body["is_open"])
        self.assertEqual(sorted(p["name"] for p in body["products"]), ["Player membership", "Volunteer"])
        self.assertEqual(body["club"]["name"], "News FC")
        self.assertEqual([p["name"] for p in body["positions"]], ["Forward"])

    def test_platform_host_is_404(self):
        self.assertEqual(Client().get(BASE + "/public/registration", HTTP_HOST="testserver").status_code, 404)

    def test_preview(self):
        ProductRegistrantDiscountTier.objects.create(product=self.product, min_registrants=2, discount_type=DiscountType.PERCENTAGE, discount_amount=Decimal("10"))
        r = self.public("post", "/preview", {"entries": [self.entry(), self.entry("Two")]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["total"], r.json()["lines"][0]["discount"]), ("180.00", "10.00"))
        self.assertEqual(RegistrationBatch.objects.count(), 0)  # nothing saved
        self.assertEqual(self.public("post", "/preview", {"entries": [self.entry(variant_id="00000000-0000-0000-0000-000000000000")]}).status_code, 422)


class SystemCategoryTests(RegistrationMixin, TestCase):
    """A membership product in the Player / Volunteer system category is offered only for that kind; any other product for both."""

    def setUp(self):
        super().setUp()
        self.player_cat = ProductCategory.objects.get(system_key="player")
        self.volunteer_cat = ProductCategory.objects.get(system_key="volunteer")
        self.product.category = self.player_cat
        self.product.save()
        self.free.category = self.volunteer_cat
        self.free.save()
        self.either = Product.objects.create(name="Family pass", price=Decimal("5.00"), product_type=Product.ProductType.MEMBERSHIP, season=self.season)
        self.either_variant = ProductVariant.objects.create(product=self.either, name="Family pass")

    def test_page_says_which_kind_each_product_is_for(self):
        kinds = {p["name"]: p["entry_kind"] for p in self.public("get", "").json()["products"]}
        self.assertEqual(kinds, {"Player membership": "player", "Volunteer": "volunteer", "Family pass": None})

    def test_submit_checks_the_option_against_the_kind(self):
        r = self.register([self.entry(kind="volunteer")])  # a volunteer cannot take the player option
        self.assertEqual(r.status_code, 422)
        self.assertIn("entries.0.variant_id", r.json()["errors"])
        r = self.register([self.entry(kind="player", variant_id=str(self.free_variant.pk))])
        self.assertIn("entries.0.variant_id", r.json()["errors"])
        self.assertEqual(RegistrationBatch.objects.count(), 0)

    def test_matching_and_uncategorised_options_are_accepted(self):
        batch = self.batch(entries=[self.entry("A", kind="player"), self.entry("B", kind="volunteer", variant_id=str(self.free_variant.pk)), self.entry("C", kind="volunteer", variant_id=str(self.either_variant.pk))])
        self.assertEqual(batch.details.count(), 3)
        self.assertEqual(self.register([self.entry("D", kind="player", variant_id=str(self.either_variant.pk))]).status_code, 201)  # no system category: both kinds

    def test_preview_checks_the_option_against_the_kind(self):
        r = self.public("post", "/preview", {"entries": [self.entry(kind="volunteer")]})
        self.assertEqual(r.status_code, 422)
        self.assertIn("entries.0.variant_id", r.json()["errors"])
        self.assertEqual(self.public("post", "/preview", {"entries": [self.entry(kind="player")]}).status_code, 200)


class SubmitTests(RegistrationMixin, TestCase):
    def test_child_with_a_parent_contact(self):
        before = Member.objects.count()
        batch = self.batch(entries=[self.entry(requested_team_id=str(self.team.pk), requested_position_id=str(self.position.pk), requested_jersey_number=9)])
        self.assertEqual(Member.objects.count(), before + 2)
        d = batch.details.get()
        self.assertEqual((d.member.first_name, d.entry_kind, d.requested_team, d.requested_jersey_number), ("Kid", "player", self.team, 9))
        m = d.membership
        self.assertEqual((m.status, m.fee_amount, m.season), (ClubMembership.StatusChoices.PENDING, Decimal("100.00"), self.season))
        family = Family.objects.get(memberships__member=batch.contact)
        self.assertEqual(sorted(FamilyMembership.objects.filter(family=family).values_list("role", flat=True)), ["child", "parent"])
        self.assertFalse(Member.objects.filter(user__isnull=False, email="pat@join.test").exists())  # no login
        self.assertFalse(ClubMembership.objects.filter(member=batch.contact).exists())  # a pure guardian

    def test_confirmation_email_has_the_status_link_and_no_money(self):
        batch = self.batch()
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["pat@join.test"])
        self.assertIn(f"/join/status/{batch.status_token}", sent.body)
        self.assertNotIn("100", sent.body)
        batch.refresh_from_db()
        self.assertIsNotNone(batch.confirmation_sent_at)

    def test_adult_registers_themselves_and_a_child(self):
        batch = self.batch(entries=[{"who": "self", "date_of_birth": "1985-01-01", "variant_id": str(self.variant.pk), "photo_public_consent": True}, self.entry()])
        self.assertEqual(batch.details.count(), 2)
        mine = batch.details.get(member=batch.contact)
        self.assertEqual((mine.member.first_name, mine.member.photo_public_consent, mine.membership.fee_amount), ("Pat", True, Decimal("100.00")))

    def test_volunteer_with_a_free_product(self):
        batch = self.batch(entries=[{"who": "self", "kind": "volunteer", "variant_id": str(self.free_variant.pk)}])
        d = batch.details.get()
        self.assertEqual((d.entry_kind, d.membership.fee_amount), ("volunteer", Decimal("0.00")))

    def test_an_existing_person_is_never_matched_by_email(self):
        existing = Member.objects.filter(email="ann@news.test").count()
        self.batch(contact=self.contact(first_name="Ann", last_name="Test", email="ann@news.test"), entries=[{"who": "self", "variant_id": str(self.variant.pk)}])
        self.assertEqual(Member.objects.filter(email="ann@news.test").count(), existing + 1)

    def test_early_bird_is_stored_on_the_membership(self):
        self.product.early_bird_discount_type, self.product.early_bird_discount_amount, self.product.early_bird_deadline = DiscountType.FIXED_AMOUNT, Decimal("15"), TODAY + datetime.timedelta(days=3)
        self.product.save()
        m = self.batch().details.get().membership
        self.assertEqual((m.fee_amount, m.early_payment_discount, m.early_payment_deadline), (Decimal("100.00"), Decimal("15.00"), self.product.early_bird_deadline))

    def test_honeypot_pretends_success_and_saves_nothing(self):
        r = self.register(website="http://spam.example")
        self.assertEqual(r.status_code, 201)
        self.assertEqual((RegistrationBatch.objects.count(), len(mail.outbox)), (0, 0))

    def test_validation_errors_are_keyed(self):
        r = self.register(
            contact={"first_name": "", "last_name": "X", "email": "nope"}, entries=[{"who": "child", "first_name": "K", "last_name": "S", "variant_id": str(self.variant.pk)}, {"who": "self", "variant_id": "00000000-0000-0000-0000-000000000000"}]
        )
        self.assertEqual(r.status_code, 422)
        errors = r.json()["errors"]
        for key in ("contact.first_name", "contact.email", "entries.0.date_of_birth", "entries.1.variant_id"):
            self.assertIn(key, errors)
        self.assertEqual(RegistrationBatch.objects.count(), 0)
        self.assertEqual(self.register(entries=[]).status_code, 422)
        two_selves = [{"who": "self", "variant_id": str(self.variant.pk)}] * 2
        self.assertIn("entries", self.register(entries=two_selves).json()["errors"])

    def test_failure_leaves_nothing_behind(self):
        before = Member.objects.count()
        self.register(entries=[self.entry(), self.entry("Bad", requested_team_id="00000000-0000-0000-0000-000000000000")])
        self.assertEqual(Member.objects.count(), before)

    def test_throttling(self):
        codes = [self.public("post", "/preview", {"entries": [self.entry()]}).status_code for _ in range(62)]
        self.assertEqual(codes[:60], [200] * 60)
        self.assertEqual(set(codes[60:]), {429})
        cache.clear()
        for _ in range(5):
            self.register(contact=self.contact(email="same@join.test"))
        r = self.register(contact=self.contact(email="same@join.test"))
        self.assertEqual((r.status_code, r.json()["code"]), (429, "rate_limited"))
        self.assertIn("Retry-After", r)


class ReviewTests(RegistrationMixin, TestCase):
    def test_unconfirmed_registrations_stay_out_of_the_queue_and_lists(self):
        batch = self.batch()
        mid = batch.details.get().membership_id
        self.assertEqual(self.staff("get", "/signups").json()["count"], 0)
        self.assertEqual(self.staff("get", "/signups/summary").json()["pending"], 0)
        listed = [m["id"] for m in self.staff("get", f"/memberships?season={self.season.pk}&limit=100").json()["items"]]
        self.assertNotIn(str(mid), listed)
        self.assertEqual(self.staff("get", "/registrations").json()["awaiting_count"], 1)
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/confirm", {"send": False}).status_code, 200)
        queue = self.staff("get", "/signups").json()
        self.assertEqual(queue["count"], 1)
        self.assertEqual(queue["results"][0]["registration"]["registration_id"], str(batch.pk))

    def test_unconfirmed_registrations_are_not_counted_as_members(self):
        """The members list and the overview numbers leave out people of a registration whose invoice is not confirmed yet."""
        batch = self.batch(entries=[self.entry("Hidden", "Kid")])
        person = batch.details.get().member_id

        def listed():
            return [m["id"] for m in self.staff("get", "/members?limit=100&include_guardians=true").json()["items"]]

        def totals():
            return self.staff("get", "/overview").json()["kpis"]["total_members"]

        before = totals()
        self.assertNotIn(str(person), listed())
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/confirm", {"send": False}).status_code, 200)
        self.assertIn(str(person), listed())
        self.assertEqual(totals(), before + 1)

    def test_review_prices_exclusion_and_manual_discount(self):
        batch = self.batch(entries=[self.entry("One"), self.entry("Two")])
        one, two = list(batch.details.order_by("created"))
        r = self.staff("patch", f"/registrations/{batch.pk}", {"lines": [{"id": str(one.pk), "price": "80.00"}, {"id": str(two.pk), "excluded": True}], "manual_discount_amount": "10.00", "manual_discount_note": "Sibling"})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body["subtotal"], body["total"], body["manual_discount_note"]), ("80.00", "70.00", "Sibling"))
        one.membership.refresh_from_db()
        two.membership.refresh_from_db()
        self.assertEqual((one.membership.fee_amount, two.membership.fee_amount), (Decimal("70.00"), Decimal("0.00")))
        back = self.staff("patch", f"/registrations/{batch.pk}", {"lines": [{"id": str(one.pk), "price": None, "excluded": None}, {"id": str(two.pk), "excluded": False}], "manual_discount_amount": "0"}).json()
        self.assertEqual(back["total"], "200.00")
        self.assertEqual(self.staff("patch", f"/registrations/{batch.pk}", {"manual_discount_amount": "999"}).status_code, 422)
        self.assertEqual(self.staff("patch", f"/registrations/{batch.pk}", {"lines": [{"id": str(batch.pk), "price": "1"}]}).status_code, 422)

    def test_confirm_numbers_the_invoice_places_the_team_and_emails_it(self):
        batch = self.batch(entries=[self.entry(requested_team_id=str(self.team.pk), requested_position_id=str(self.position.pk), requested_jersey_number=7)])
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            r = self.staff("post", f"/registrations/{batch.pk}/confirm", {})
        self.assertEqual(r.status_code, 200, r.content)
        year = timezone.localdate().year
        self.assertEqual(r.json()["invoice_number"], f"REG-{year}-0001")
        d = batch.details.get()
        spot = TeamMembership.objects.get(member=d.member, team=self.team, season=self.season)
        self.assertEqual((spot.jersey_number, spot.position), (7, self.position))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(f"REG-{year}-0001", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ["pat@join.test"])
        batch.refresh_from_db()
        self.assertIsNotNone(batch.invoice_sent_at)
        second = self.batch()
        self.assertEqual(self.staff("post", f"/registrations/{second.pk}/confirm", {"send": False}).json()["invoice_number"], f"REG-{year}-0002")
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/confirm", {}).status_code, 422)
        self.assertEqual(self.staff("patch", f"/registrations/{batch.pk}", {"manual_discount_amount": "5"}).json()["code"], "confirmed")

    def test_a_taken_jersey_number_is_dropped_not_an_error(self):
        # both numbers are free when they register; somebody else takes 8 before staff confirm the invoice (placement then drops it, no error)
        batch = self.batch(entries=[self.entry(requested_team_id=str(self.team.pk), requested_jersey_number=7), self.entry("Two", requested_team_id=str(self.team.pk), requested_jersey_number=8)])
        holder = Member.objects.create(first_name="Late", last_name="Holder")
        TeamMembership.objects.create(team=self.team, member=holder, season=self.season, jersey_number=8)
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/confirm", {"send": False}).status_code, 200)
        self.assertEqual(sorted(str(n) for n in TeamMembership.objects.filter(team=self.team, member__registration_details__batch=batch).values_list("jersey_number", flat=True)), ["7", "None"])

    def test_cancel_removes_the_people_who_are_only_this_sign_up(self):
        batch = self.batch()
        kid = batch.details.get().member_id
        r = self.staff("post", f"/registrations/{batch.pk}/cancel")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "cancelled")
        self.assertFalse(Member.objects.filter(pk=kid).exists())
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/cancel").status_code, 422)

    def test_resend_and_pdf(self):
        batch = self.batch()
        mail.outbox.clear()
        self.assertTrue(self.staff("post", f"/registrations/{batch.pk}/resend-confirmation").json()["sent"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/resend-invoice").status_code, 409)
        self.staff("post", f"/registrations/{batch.pk}/confirm", {"send": False})
        self.assertIn(self.staff("get", f"/registrations/{batch.pk}/invoice.pdf").status_code, (200, 503))

    def test_settings(self):
        self.assertTrue(self.staff("get", "/registration/settings").json()["is_open"])
        r = self.staff("put", "/registration/settings", {"is_open": False, "intro_text": "Welcome", "invoice_due_days": 14})
        self.assertEqual((r.json()["is_open"], r.json()["intro_text"], r.json()["invoice_due_days"]), (False, "Welcome", 14))
        self.assertEqual(self.staff("put", "/registration/settings", {"invoice_due_days": 999}).status_code, 422)
        self.assertEqual(self.public("get", "").json()["intro_text"], "Welcome")

    def test_staff_endpoints_need_admin(self):
        batch = self.batch()
        for method, path in [("get", "/registrations"), ("get", f"/registrations/{batch.pk}"), ("post", f"/registrations/{batch.pk}/confirm"), ("get", "/registration/settings"), ("put", "/registration/settings")]:
            self.assertEqual(self.staff(method, path, {} if method in ("post", "put") else None, user=self.cara_user).status_code, 403, path)


class StatusPageTests(RegistrationMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.requirement = onboarding.create_requirement(self.owner, name="Medical certificate", requires_document=True)
        self.note = onboarding.create_requirement(self.owner, name="Read the rules", requires_document=False)

    def upload(self, batch, requirement=None, member=None, name="cert.pdf"):
        data = {"document": SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")}
        if member is not None:
            data["member_id"] = str(member)
        return Client().post(BASE + f"/public/registration/status/{batch.status_token}/documents/{(requirement or self.requirement).pk}", data, HTTP_HOST=self.host)

    def test_status_page_needs_the_token(self):
        self.assertEqual(self.public("get", "/status/not-a-token").status_code, 404)
        batch = self.batch()
        body = self.public("get", f"/status/{batch.status_token}").json()
        self.assertEqual((body["status"], body["people"][0]["name"], body["people"][0]["fee"]), ("awaiting", "Kid Sharks", None))  # no money before the review
        items = {i["name"]: i for i in body["people"][0]["checklist"]}
        self.assertTrue(items["Medical certificate"]["can_upload"])
        self.assertFalse(items["Read the rules"]["can_upload"])

    def test_fee_and_early_bird_after_confirmation(self):
        self.product.early_bird_discount_type, self.product.early_bird_discount_amount, self.product.early_bird_deadline = DiscountType.FIXED_AMOUNT, Decimal("10"), TODAY + datetime.timedelta(days=2)
        self.product.save()
        batch = self.batch()
        self.staff("post", f"/registrations/{batch.pk}/confirm", {"send": False})
        body = self.public("get", f"/status/{batch.status_token}").json()
        fee = body["people"][0]["fee"]
        self.assertEqual((body["status"], fee["fee_amount"], fee["amount_due"], fee["early_bird"]["fee_if_paid_in_time"]), ("confirmed", "100.00", "90.00", "90.00"))
        self.assertTrue(body["invoice_number"].startswith("REG-"))

    def test_upload_resolves_the_item_once(self):
        batch = self.batch()
        r = self.upload(batch)
        self.assertEqual(r.status_code, 201, r.content)
        item = next(i for i in r.json()["people"][0]["checklist"] if i["name"] == "Medical certificate")
        self.assertEqual((item["state"], item["has_document"], item["can_upload"]), ("complete", True, False))
        status = batch.details.get().membership.requirement_statuses.get(requirement=self.requirement)
        self.assertEqual((status.is_complete, status.completed_by, "registrant" in status.note), (True, None, True))
        again = self.upload(batch)
        self.assertEqual((again.status_code, again.json()["code"]), (422, "already_resolved"))

    def test_upload_rules(self):
        batch = self.batch()
        self.assertEqual(self.upload(batch, self.note).json()["code"], "not_required")
        self.assertEqual(self.upload(batch, name="run.exe").status_code, 422)
        self.assertEqual(self.upload(batch, member="00000000-0000-0000-0000-000000000000").json()["code"], "unknown_person")
        self.assertEqual(Client().post(BASE + "/public/registration/status/bad-token/documents/" + str(self.requirement.pk), {"document": SimpleUploadedFile("a.pdf", b"x")}, HTTP_HOST=self.host).status_code, 404)
        self.staff("post", f"/registrations/{batch.pk}/cancel")
        self.assertEqual(self.upload(batch).json()["code"], "cancelled")

    def test_with_two_people_the_document_says_who(self):
        batch = self.batch(entries=[self.entry("One"), self.entry("Two")])
        _, two = list(batch.details.order_by("created"))
        self.assertEqual(self.upload(batch).json()["code"], "unknown_person")
        self.assertEqual(self.upload(batch, member=two.member_id).status_code, 201)
        body = self.public("get", f"/status/{batch.status_token}").json()
        states = {p["name"]: next(i for i in p["checklist"] if i["name"] == "Medical certificate")["state"] for p in body["people"]}
        self.assertEqual(states, {"One Sharks": "open", "Two Sharks": "complete"})


class LinkingTests(RegistrationMixin, TestCase):
    def sign_up_as_eve(self):
        batch = self.batch(contact=self.contact(first_name="Eve", last_name="Test", email="eve@news.test"), entries=[{"who": "self", "variant_id": str(self.variant.pk), "requested_team_id": str(self.team.pk)}])
        self.staff("post", f"/registrations/{batch.pk}/confirm", {"send": False})
        return batch, batch.details.get().membership

    def test_duplicates_and_link(self):
        batch, membership = self.sign_up_as_eve()
        rows = self.staff("get", f"/signups/{membership.pk}/duplicates").json()["results"]
        self.assertEqual([(r["name"], r["same_email"], r["has_membership"]) for r in rows], [("Eve Test", True, False)])
        duplicate = membership.member_id
        r = self.staff("post", f"/signups/{membership.pk}/link", {"member_id": rows[0]["member_id"]})
        self.assertEqual(r.status_code, 200, r.content)
        membership.refresh_from_db()
        self.assertEqual(str(membership.member_id), rows[0]["member_id"])
        self.assertFalse(Member.objects.filter(pk=duplicate).exists())
        self.assertTrue(TeamMembership.objects.filter(member=membership.member, team=self.team).exists())  # the roster spot moved too
        self.assertEqual(batch.details.get().member_id, membership.member_id)
        self.assertEqual(Member.objects.filter(email="eve@news.test").count(), 1)

    def test_cannot_link_to_someone_who_already_has_a_membership(self):
        _, membership = self.sign_up_as_eve()
        r = self.staff("post", f"/signups/{membership.pk}/link", {"member_id": str(self.bob.pk)})
        self.assertEqual((r.status_code, r.json()["code"]), (422, "already_member"))
        self.assertEqual(self.staff("post", f"/signups/{membership.pk}/link", {"member_id": str(membership.member_id)}).status_code, 422)

    def test_needs_admin(self):
        _, membership = self.sign_up_as_eve()
        self.assertEqual(self.staff("get", f"/signups/{membership.pk}/duplicates", user=self.cara_user).status_code, 403)


class ProductPricingFieldsTests(RegistrationMixin, TestCase):
    def setUp(self):
        super().setUp()
        from features.entitlements import Entitlement, Entitlements

        self.features = Entitlements({"shop": Entitlement(True)})
        for target in ("features.middleware.entitlements_for_request", "features.context.get_current_entitlements", "features.backends.get_current_entitlements"):
            patcher = mock.patch(target, side_effect=lambda *args: self.features)
            patcher.start()
            self.addCleanup(patcher.stop)

    def membership_payload(self, **extra):
        return {"name": "U12 membership", "price": "120.00", "product_type": "membership", "season_id": str(self.season.pk), "early_bird_discount_type": "percentage", "early_bird_discount_amount": "10",
                "early_bird_deadline": str(TODAY + datetime.timedelta(days=7)), "registrant_discount_scope": "per_order", "registrant_tiers": [{"min_registrants": 3, "discount_type": "fixed_amount", "discount_amount": "20.00"}], **extra}  # fmt: skip

    def test_product_api_carries_the_registration_pricing(self):
        r = self.staff("post", "/shop/products", self.membership_payload())
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual((body["season_name"], body["early_bird_discount_type"], body["registrant_discount_scope"]), (self.season.name, "percentage", "per_order"))
        self.assertEqual(body["registrant_tiers"], [{"min_registrants": 3, "discount_type": "fixed_amount", "discount_amount": "20.00"}])
        patched = self.staff("patch", f"/shop/products/{body['id']}", {"registrant_tiers": [{"min_registrants": 2, "discount_type": "percentage", "discount_amount": "5"}], "early_bird_discount_type": ""}).json()
        self.assertEqual([t["min_registrants"] for t in patched["registrant_tiers"]], [2])
        self.assertEqual((patched["early_bird_discount_type"], patched["early_bird_deadline"]), ("", None))

    def test_registration_pricing_is_for_membership_products_only(self):
        r = self.staff("post", "/shop/products", self.membership_payload(product_type="merchandise", registrant_tiers=[]))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.staff("post", "/shop/products", self.membership_payload(early_bird_deadline=None)).status_code, 422)
        self.assertEqual(self.staff("post", "/shop/products", self.membership_payload(registrant_tiers=[{"min_registrants": 1, "discount_type": "percentage", "discount_amount": "5"}])).status_code, 422)
        plain = self.staff("post", "/shop/products", {"name": "Cap", "price": "5.00"}).json()
        self.assertEqual((plain["season_id"], plain["registrant_tiers"], plain["registrant_discount_scope"]), (None, [], "per_person"))

    # --- a team on membership products and variants ---------------------------------------------------------------------------------

    def test_team_on_product_and_variant_through_the_api(self):
        payload = {**self.membership_payload(early_bird_discount_type="", early_bird_deadline=None, registrant_tiers=[]), "team_id": str(self.team.pk)}
        body = self.staff("post", "/shop/products", {**payload, "variants": [{"name": "U14", "team_id": str(self.other_team.pk)}, {"name": "U12"}]}).json()
        self.assertEqual((body["team_id"], body["team_name"]), (str(self.team.pk), self.team.name))
        teams = {v["name"]: v["team_name"] for v in body["variants"]}
        self.assertEqual(teams, {"U14": self.other_team.name, "U12": None})
        variant = next(v for v in body["variants"] if v["name"] == "U12")
        r = self.staff("patch", f"/shop/products/{body['id']}/variants/{variant['id']}", {"team_id": str(self.other_team.pk)})
        self.assertEqual((r.status_code, r.json()["team_id"]), (200, str(self.other_team.pk)))
        r = self.staff("patch", f"/shop/products/{body['id']}/variants/{variant['id']}", {"team_id": None})
        self.assertIsNone(r.json()["team_id"])
        added = self.staff("post", f"/shop/products/{body['id']}/variants", {"name": "U16", "team_id": str(self.other_team.pk)})
        self.assertEqual((added.status_code, added.json()["team_id"]), (201, str(self.other_team.pk)))
        cleared = self.staff("patch", f"/shop/products/{body['id']}", {"team_id": None}).json()
        self.assertIsNone(cleared["team_id"])
        self.assertEqual(self.staff("patch", f"/shop/products/{body['id']}", {"team_id": "00000000-0000-0000-0000-000000000000"}).status_code, 422)

    def test_a_team_is_refused_for_volunteer_and_other_products(self):
        volunteer = ProductCategory.objects.get(system_key="volunteer")
        player = ProductCategory.objects.get(system_key="player")
        plain = self.membership_payload(early_bird_discount_type="", early_bird_deadline=None, registrant_tiers=[])
        r = self.staff("post", "/shop/products", {**plain, "category_id": str(volunteer.pk), "team_id": str(self.team.pk)})
        self.assertEqual(r.status_code, 422)
        self.assertIn("team_id", r.json()["errors"])
        merch = self.staff("post", "/shop/products", {"name": "Cap", "price": "5.00", "team_id": str(self.team.pk)})
        self.assertEqual(merch.status_code, 422)
        plain = self.membership_payload(early_bird_discount_type="", early_bird_deadline=None, registrant_tiers=[])
        ok = self.staff("post", "/shop/products", {**plain, "category_id": str(player.pk), "team_id": str(self.team.pk), "variants": [{"name": "U12", "team_id": str(self.team.pk)}]}).json()
        self.assertEqual(ok["team_id"], str(self.team.pk))
        v = ok["variants"][0]
        # asking for a variant team on a volunteer product is refused too
        vol = self.staff("post", "/shop/products", {**plain, "category_id": str(volunteer.pk)}).json()
        self.assertEqual(self.staff("post", f"/shop/products/{vol['id']}/variants", {"name": "X", "team_id": str(self.team.pk)}).status_code, 422)
        # switching the category to Volunteer (or the type away from membership) drops the links quietly
        moved = self.staff("patch", f"/shop/products/{ok['id']}", {"category_id": str(volunteer.pk)}).json()
        self.assertEqual((moved["team_id"], moved["variants"][0]["team_id"]), (None, None))
        self.assertEqual(v["team_id"], str(self.team.pk))


class JerseyNumberTests(RegistrationMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.pool = NumberPool.objects.create(name="Juniors", min_number=1, max_number=5, min_age_gap_years=5)
        self.team.pool = self.pool
        self.team.save()
        self.other_team.pool = self.pool
        self.other_team.save()

    def numbers(self, team=None, **params):
        query = "&".join(f"{k}={v}" for k, v in {"team_id": (team or self.team).pk, "variant_id": self.variant.pk, **params}.items())
        return self.public("get", f"/numbers?{query}")

    def asking(self, number, team=None, **extra):
        return self.entry(requested_team_id=str((team or self.team).pk), requested_jersey_number=number, **extra)

    def test_the_endpoint_lists_the_free_numbers_without_names(self):
        holder = Member.objects.create(first_name="Secret", last_name="Holder", date_of_birth=datetime.date(2014, 1, 1))
        TeamMembership.objects.create(team=self.team, member=holder, season=self.season, jersey_number=2)
        NumberReservation.objects.create(pool=self.pool, number=4)
        r = self.numbers(date_of_birth="2015-05-05", exclude="5")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body["numbers"], body["taken"]), ([1, 3], [2, 4, 5]))
        self.assertEqual(body["pool"], {"name": "Juniors", "min": 1, "max": 5})
        self.assertNotIn("Secret", r.content.decode())

    def test_the_endpoint_for_a_team_without_a_pool(self):
        self.team.pool = None
        self.team.save()
        holder = Member.objects.create(first_name="A", last_name="B")
        TeamMembership.objects.create(team=self.team, member=holder, season=self.season, jersey_number=11)
        body = self.numbers().json()
        self.assertEqual((body["pool"], body["numbers"], body["taken"]), (None, None, [11]))

    def test_the_endpoint_needs_a_known_team_and_a_season(self):
        self.assertEqual(self.public("get", f"/numbers?team_id={self.club.pk}&variant_id={self.variant.pk}").status_code, 404)
        self.assertEqual(self.public("get", f"/numbers?team_id={self.team.pk}").json()["numbers"], [1, 2, 3, 4, 5])  # one offerable season: no variant needed
        later = type(self.season).objects.create(start_date=TODAY + datetime.timedelta(days=400), end_date=TODAY + datetime.timedelta(days=700))
        other = Product.objects.create(name="Next", price=Decimal("10"), product_type=Product.ProductType.MEMBERSHIP, season=later)
        ProductVariant.objects.create(product=other, name="Next")
        r = self.public("get", f"/numbers?team_id={self.team.pk}")
        self.assertEqual((r.status_code, r.json()["code"]), (422, "no_season"))

    def test_the_endpoint_is_throttled(self):
        with mock.patch("api.routers.public_registration.LIMITS", {"numbers": 2, "preview": 60, "submit": 10, "status": 120, "upload": 20}):
            codes = [self.numbers().status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])

    def test_a_free_number_is_accepted_and_stored(self):
        batch = self.batch(entries=[self.asking(3)])
        self.assertEqual(batch.details.get().requested_jersey_number, 3)

    def test_out_of_range_reserved_and_held_numbers_are_refused(self):
        NumberReservation.objects.create(pool=self.pool, number=4)
        holder = Member.objects.create(first_name="H", last_name="H", date_of_birth=datetime.date(2015, 1, 1))
        TeamMembership.objects.create(team=self.team, member=holder, season=self.season, jersey_number=2)
        for number in (9, 4, 2):
            r = self.register(entries=[self.asking(number)])
            self.assertEqual(r.status_code, 422, number)
            self.assertIn("entries.0.requested_jersey_number", r.json()["errors"], number)
        self.assertNotIn("H H", json.dumps(r.json()))  # who holds it is never told
        self.assertEqual(RegistrationBatch.objects.count(), 0)

    def test_the_age_gap_decides_on_the_registrants_birthday(self):
        holder = Member.objects.create(first_name="H", last_name="H", date_of_birth=datetime.date(2000, 1, 1))
        TeamMembership.objects.create(team=self.other_team, member=holder, season=self.season, jersey_number=2)
        self.assertEqual(self.register(entries=[self.asking(2, date_of_birth="2015-05-05")]).status_code, 201)
        self.assertEqual(self.register(entries=[self.asking(2, date_of_birth="2001-05-05")]).status_code, 422)

    def test_two_people_in_one_registration_cannot_ask_for_the_same_number(self):
        r = self.register(entries=[self.asking(3, first="One"), self.asking(3, first="Two")])
        self.assertEqual(r.status_code, 422)
        self.assertNotIn("entries.0.requested_jersey_number", r.json()["errors"])
        self.assertIn("entries.1.requested_jersey_number", r.json()["errors"])
        self.assertEqual(self.register(entries=[self.asking(3, first="One"), self.asking(4, first="Two")]).status_code, 201)

    def test_siblings_on_different_teams_of_the_pool_may_share_with_an_age_gap(self):
        two = [self.asking(3, first="Old", date_of_birth="2000-01-01", team=self.team), self.asking(3, first="Young", date_of_birth="2015-01-01", team=self.other_team)]
        self.assertEqual(self.register(entries=two).status_code, 201)
        close = [self.asking(3, first="Old", date_of_birth="2014-01-01", team=self.team), self.asking(3, first="Young", date_of_birth="2015-01-01", team=self.other_team)]
        r = self.register(entries=close)
        self.assertEqual(r.status_code, 422)
        self.assertIn("entries.1.requested_jersey_number", r.json()["errors"])

    def test_a_team_without_a_pool_only_needs_a_free_number_up_to_999(self):
        self.team.pool = None
        self.team.save()
        holder = Member.objects.create(first_name="H", last_name="H")
        TeamMembership.objects.create(team=self.team, member=holder, season=self.season, jersey_number=11)
        self.assertEqual(self.register(entries=[self.asking(11)]).status_code, 422)
        self.assertEqual(self.register(entries=[self.asking(1000)]).status_code, 422)
        self.assertEqual(self.register(entries=[self.asking(123)]).status_code, 201)

    def test_volunteers_are_not_checked(self):
        r = self.register(entries=[self.asking(99, kind="volunteer", variant_id=str(self.free_variant.pk))])
        self.assertEqual(r.status_code, 201, r.content)


class TiersPerProductTests(RegistrationMixin, TestCase):
    """A registrant tier counts the people registered for THAT product (all its variants), not everybody in the form."""

    def setUp(self):
        super().setUp()
        self.youth = Product.objects.create(name="Youth", price=Decimal("100.00"), product_type=Product.ProductType.MEMBERSHIP, season=self.season)
        self.u_a = ProductVariant.objects.create(product=self.youth, name="U8")
        self.u_b = ProductVariant.objects.create(product=self.youth, name="U10")
        self.d1 = Product.objects.create(name="D1", price=Decimal("200.00"), product_type=Product.ProductType.MEMBERSHIP, season=self.season)
        self.d1_variant = ProductVariant.objects.create(product=self.d1, name="D1")
        for product in (self.youth, self.d1):
            for n, pct in ((2, 10), (3, 20)):
                ProductRegistrantDiscountTier.objects.create(product=product, min_registrants=n, discount_type=DiscountType.PERCENTAGE, discount_amount=Decimal(pct))

    def prices(self, *variants):
        return pricing.price_entries(list(variants)).lines

    def test_one_youth_and_one_d1_get_no_discount(self):
        lines = self.prices(self.u_a, self.d1_variant)
        self.assertEqual([ln.discount for ln in lines], [Decimal("0.00"), Decimal("0.00")])

    def test_two_youth_and_one_d1_only_the_youth_tier_applies(self):
        lines = self.prices(self.u_a, self.d1_variant, self.u_b)
        self.assertEqual([ln.price for ln in lines], [Decimal("90.00"), Decimal("200.00"), Decimal("90.00")])  # variants of the product add up

    def test_three_youth_reach_the_third_tier(self):
        lines = self.prices(self.u_a, self.u_a, self.u_b)
        self.assertEqual([ln.price for ln in lines], [Decimal("80.00")] * 3)

    def test_a_per_order_fixed_tier_is_shared_over_that_products_lines_only(self):
        self.youth.registrant_discount_scope = Product.RegistrantDiscountScope.PER_ORDER
        self.youth.save()
        ProductRegistrantDiscountTier.objects.filter(product=self.youth).delete()
        ProductRegistrantDiscountTier.objects.create(product=self.youth, min_registrants=2, discount_type=DiscountType.FIXED_AMOUNT, discount_amount=Decimal("30.00"))
        lines = self.prices(self.u_a, self.d1_variant, self.u_b)
        self.assertEqual(sum(ln.discount for ln in lines if ln.product == self.youth), Decimal("30.00"))
        self.assertEqual(sum(ln.discount for ln in lines if ln.product == self.d1), Decimal("0.00"))

    def test_the_preview_endpoint_follows_the_same_rule(self):
        def total(*variants):
            r = self.public("post", "/preview", {"entries": [{**self.entry(), "variant_id": str(v.pk)} for v in variants]})
            self.assertEqual(r.status_code, 200, r.content)
            return Decimal(r.json()["total"])

        self.assertEqual(total(self.u_a, self.d1_variant), Decimal("300.00"))
        self.assertEqual(total(self.u_a, self.u_b, self.d1_variant), Decimal("380.00"))


class VariantlessProductTests(RegistrationMixin, TestCase):
    """A membership product without variants is offered as it is: one option at the product price."""

    def setUp(self):
        super().setUp()
        self.plain = Product.objects.create(name="Supporter", price=Decimal("25.00"), product_type=Product.ProductType.MEMBERSHIP, season=self.season)
        self.key = f"product:{self.plain.pk}"

    def offer(self, name):
        products = {p["name"]: p for p in self.public("get", "").json()["products"]}
        return products.get(name)

    def test_it_is_offered_as_a_single_option_named_like_the_product(self):
        offered = self.offer("Supporter")
        self.assertEqual([(v["option_id"], v["name"], v["price"], v["id"]) for v in offered["variants"]], [(self.key, "Supporter", "25.00", str(self.plain.pk))])
        with_variants = self.offer("Player membership")
        self.assertEqual(with_variants["variants"][0]["option_id"], f"variant:{self.variant.pk}")  # products with variants are unchanged

    def test_a_product_whose_variants_are_all_inactive_is_not_offered(self):
        ProductVariant.objects.filter(pk=self.variant.pk).update(is_active=False)
        self.assertIsNone(self.offer("Player membership"))
        self.assertIsNotNone(self.offer("Supporter"))

    def test_the_old_variant_id_still_works(self):
        r = self.public("post", "/preview", {"entries": [self.entry()]})
        self.assertEqual((r.status_code, r.json()["total"]), (200, "100.00"))

    def test_preview_prices_it_and_tiers_and_early_bird_use_the_product(self):
        self.plain.early_bird_discount_type, self.plain.early_bird_discount_amount, self.plain.early_bird_deadline = DiscountType.PERCENTAGE, Decimal("20"), TODAY + datetime.timedelta(days=3)
        self.plain.save()
        ProductRegistrantDiscountTier.objects.create(product=self.plain, min_registrants=2, discount_type=DiscountType.FIXED_AMOUNT, discount_amount=Decimal("5.00"))
        one = self.public("post", "/preview", {"entries": [{**self.entry(), "variant_id": None, "option_id": self.key}]}).json()
        self.assertEqual((one["lines"][0]["description"], one["total"], one["early_bird_discount"]), ("Supporter", "25.00", "5.00"))
        two = self.public("post", "/preview", {"entries": [{**self.entry(), "variant_id": None, "option_id": self.key}] * 2}).json()
        self.assertEqual(two["total"], "40.00")  # 2 x (25 - 5)

    def test_submit_stores_the_product_and_the_review_shows_it(self):
        batch = self.batch(entries=[{**self.entry(), "variant_id": None, "option_id": self.key}])
        d = batch.details.get()
        self.assertEqual((d.product, d.product_variant, d.description, d.base_price), (self.plain, None, "Supporter", Decimal("25.00")))
        self.assertEqual(d.membership.fee_amount, Decimal("25.00"))
        detail = self.staff("get", f"/registrations/{batch.pk}").json()
        self.assertEqual([ln["description"] for ln in detail["lines"]], ["Supporter"])
        status = self.public("get", f"/status/{batch.status_token}").json()
        self.assertEqual(status["people"][0]["description"], "Supporter")

    def test_the_kind_category_applies_to_the_product(self):
        volunteer = ProductCategory.objects.get(system_key="volunteer")
        self.plain.category = volunteer
        self.plain.save()
        self.assertEqual(self.register(entries=[{**self.entry(), "variant_id": None, "option_id": self.key}]).status_code, 422)  # a player can't pick it
        ok = self.register(entries=[{**self.entry(), "variant_id": None, "option_id": self.key, "kind": "volunteer"}])
        self.assertEqual(ok.status_code, 201, ok.content)

    def test_an_unknown_or_malformed_option_is_refused(self):
        for bad in ("product:00000000-0000-0000-0000-000000000000", "nonsense", "variant:xyz", ""):
            r = self.register(entries=[{**self.entry(), "variant_id": None, "option_id": bad}])
            self.assertEqual(r.status_code, 422, bad)
            self.assertIn("entries.0.variant_id", r.json()["errors"], bad)

    def test_the_numbers_endpoint_takes_an_option_id(self):
        r = self.public("get", f"/numbers?team_id={self.team.pk}&option_id={self.key}")
        self.assertEqual(r.status_code, 200, r.content)


class CreditTests(RegistrationMixin, TestCase):
    """A negative price on a membership product is a credit: nobody pays less than 0, the credit is spread over the registration's positive lines."""

    def setUp(self):
        super().setUp()
        self.credit = Product.objects.create(name="Volunteer credit", price=Decimal("-20.00"), product_type=Product.ProductType.MEMBERSHIP, season=self.season)
        self.key = f"product:{self.credit.pk}"
        self.positive = pricing.Option(product=self.product, variant=self.variant)  # 100.00
        self.credit_option = pricing.Option(product=self.credit)

    def priced(self, *options):
        return pricing.price_entries(list(options), today=TODAY)

    def test_the_credit_comes_off_the_positive_line(self):
        p = self.priced(self.positive, self.credit_option)
        self.assertEqual([ln.price for ln in p.lines], [Decimal("80.00"), Decimal("0.00")])
        self.assertEqual((p.total, p.credit_total, p.credit_applied), (Decimal("80.00"), Decimal("20.00"), Decimal("20.00")))
        self.assertEqual([ln.is_credit for ln in p.lines], [False, True])

    def test_the_credit_is_spread_in_proportion_and_the_cents_are_not_lost(self):
        ten = Product.objects.create(name="Tenner", price=Decimal("10.00"), product_type=Product.ProductType.MEMBERSHIP, season=self.season)
        small = Product.objects.create(name="Small credit", price=Decimal("-10.00"), product_type=Product.ProductType.MEMBERSHIP, season=self.season)
        p = self.priced(self.positive, pricing.Option(product=ten), pricing.Option(product=small))  # 100 and 10, credit 10
        self.assertEqual([ln.credit_share for ln in p.lines[:2]], [Decimal("9.09"), Decimal("0.91")])
        self.assertEqual(p.total, Decimal("100.00"))
        three = self.priced(self.positive, self.positive, self.positive, pricing.Option(product=small))
        self.assertEqual(sum(ln.credit_share for ln in three.lines), Decimal("10.00"))  # 3.33 x 3 + the cent
        self.assertEqual(three.total, Decimal("290.00"))

    def test_only_a_credit_costs_nothing(self):
        p = self.priced(self.credit_option)
        self.assertEqual((p.lines[0].price, p.total, p.credit_total, p.credit_applied), (Decimal("0.00"), Decimal("0.00"), Decimal("20.00"), Decimal("0.00")))

    def test_a_credit_larger_than_the_rest_never_makes_the_total_negative(self):
        cheap = Product.objects.create(name="Cheap", price=Decimal("15.00"), product_type=Product.ProductType.MEMBERSHIP, season=self.season)
        p = self.priced(pricing.Option(product=cheap), self.credit_option)
        self.assertEqual((p.lines[0].price, p.total, p.credit_applied), (Decimal("0.00"), Decimal("0.00"), Decimal("15.00")))

    def test_credits_get_no_tiers_and_no_early_bird(self):
        both = Product.objects.create(name="Both", price=Decimal("50.00"), product_type=Product.ProductType.MEMBERSHIP, season=self.season)
        plus = ProductVariant.objects.create(product=both, name="Plus")
        minus = ProductVariant.objects.create(product=both, name="Minus", price=Decimal("-10.00"))
        ProductRegistrantDiscountTier.objects.create(product=both, min_registrants=2, discount_type=DiscountType.PERCENTAGE, discount_amount=Decimal("50"))
        both.early_bird_discount_type, both.early_bird_discount_amount, both.early_bird_deadline = DiscountType.PERCENTAGE, Decimal("20"), TODAY + datetime.timedelta(days=5)
        both.save()
        p = self.priced(pricing.Option(product=both, variant=plus), pricing.Option(product=both, variant=minus))
        self.assertEqual(p.lines[0].discount, Decimal("0.00"))  # only one positive registrant on the product: the credit does not make two
        self.assertEqual(p.lines[1].early_bird_discount, Decimal("0.00"))
        self.assertEqual((p.lines[0].price, p.lines[0].early_bird_discount), (Decimal("40.00"), Decimal("10.00")))  # early-bird on the 50, capped by the fee
        two = self.priced(pricing.Option(product=both, variant=plus), pricing.Option(product=both, variant=plus), pricing.Option(product=both, variant=minus))
        self.assertEqual([ln.discount for ln in two.lines], [Decimal("25.00"), Decimal("25.00"), Decimal("0.00")])

    def test_the_preview_shows_the_credit(self):
        r = self.public("post", "/preview", {"entries": [self.entry(), {**self.entry("Vol"), "variant_id": None, "option_id": self.key, "kind": "player"}]})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body["total"], body["credit_total"], body["credit_applied"]), ("80.00", "20.00", "20.00"))
        self.assertEqual([(ln["base_price"], ln["price"], ln["is_credit"], ln["credit_share"]) for ln in body["lines"]], [("100.00", "80.00", False, "20.00"), ("-20.00", "0.00", True, "0.00")])

    def batch_with_credit(self):
        return self.batch(entries=[self.entry(), {**self.entry("Vol"), "variant_id": None, "option_id": self.key}])

    def test_submit_stores_the_fees(self):
        batch = self.batch_with_credit()
        fees = {d.description: (d.base_price, d.credit_share, d.membership.fee_amount) for d in batch.details.all()}
        self.assertEqual(fees, {"Player membership": (Decimal("100.00"), Decimal("20.00"), Decimal("80.00")), "Volunteer credit": (Decimal("-20.00"), Decimal("0.00"), Decimal("0.00"))})
        only = self.batch(entries=[{**self.entry("Solo"), "variant_id": None, "option_id": self.key}])
        self.assertEqual(only.details.get().membership.fee_amount, Decimal("0.00"))

    def test_the_staff_review_shows_credit_lines_and_keeps_working(self):
        batch = self.batch_with_credit()
        detail = self.staff("get", f"/registrations/{batch.pk}").json()
        lines = {ln["description"]: ln for ln in detail["lines"]}
        self.assertEqual((lines["Volunteer credit"]["is_credit"], lines["Volunteer credit"]["price"], lines["Volunteer credit"]["payable"]), (True, "-20.00", "0.00"))
        self.assertEqual((lines["Player membership"]["credit_share"], lines["Player membership"]["payable"], lines["Player membership"]["fee_amount"]), ("20.00", "80.00", "80.00"))
        self.assertEqual((detail["subtotal"], detail["total"], detail["credit_total"], detail["credit_applied"]), ("80.00", "80.00", "20.00", "20.00"))
        credit_id, player_id = lines["Volunteer credit"]["id"], lines["Player membership"]["id"]
        # a manual discount comes off what is left after the credit
        r = self.staff("patch", f"/registrations/{batch.pk}", {"manual_discount_amount": "10.00", "manual_discount_note": "Friend"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["total"], {x["description"]: x["fee_amount"] for x in r.json()["lines"]}), ("70.00", {"Player membership": "70.00", "Volunteer credit": "0.00"}))
        # a credit line has no price to override; an override of the player line is respected and the credit follows it
        self.assertEqual(self.staff("patch", f"/registrations/{batch.pk}", {"lines": [{"id": credit_id, "price": "5.00"}]}).status_code, 422)
        r = self.staff("patch", f"/registrations/{batch.pk}", {"lines": [{"id": player_id, "price": "50.00"}], "manual_discount_amount": "0"})
        self.assertEqual(r.json()["total"], "30.00")
        # leaving the credit line out puts the full price back; leaving the player out leaves the credit unused and the total at 0
        r = self.staff("patch", f"/registrations/{batch.pk}", {"lines": [{"id": player_id, "price": None}, {"id": credit_id, "excluded": True}]})
        self.assertEqual((r.json()["total"], r.json()["credit_applied"]), ("100.00", "0.00"))
        r = self.staff("patch", f"/registrations/{batch.pk}", {"lines": [{"id": credit_id, "excluded": False}, {"id": player_id, "excluded": True}]})
        self.assertEqual((r.json()["total"], r.json()["credit_total"], r.json()["credit_applied"]), ("0.00", "20.00", "0.00"))

    def test_confirm_status_and_invoice_show_the_credit(self):
        batch = self.batch_with_credit()
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/confirm", {"send": False}).status_code, 200)
        people = {p["description"]: p for p in self.public("get", f"/status/{batch.status_token}").json()["people"]}
        self.assertEqual((people["Volunteer credit"]["is_credit"], people["Volunteer credit"]["credit"], people["Volunteer credit"]["fee"]["fee_amount"]), (True, "20.00", "0.00"))
        self.assertEqual((people["Player membership"]["credit"], people["Player membership"]["fee"]["fee_amount"]), ("20.00", "80.00"))
        mail.outbox.clear()
        self.assertTrue(self.staff("post", f"/registrations/{batch.pk}/resend-invoice").json()["sent"])
        self.assertIn("Credit: Volunteer credit", mail.outbox[0].body)
        self.assertIn("-", mail.outbox[0].body.split("Credit: Volunteer credit")[1].split("\n")[0])


class ProductTeamTests(RegistrationMixin, TestCase):
    """A membership option can carry a team: it is fixed on the join page (and on submit), staff change it while reviewing."""

    def setUp(self):
        super().setUp()
        self.pool = NumberPool.objects.create(name="Juniors", min_number=1, max_number=5, min_age_gap_years=5)
        self.team.pool = self.pool
        self.team.save()
        self.product.team = self.team
        self.product.save()
        self.u14 = ProductVariant.objects.create(product=self.product, name="U14", team=self.other_team)

    def option(self, name):
        products = self.public("get", "").json()["products"]
        return next(o for p in products for o in p["variants"] if o["name"] == name)

    def test_the_page_says_which_team_an_option_is_for(self):
        self.assertEqual((self.option("Player membership")["team_id"], self.option("Player membership")["team_name"]), (str(self.team.pk), self.team.name))  # the product's team
        self.assertEqual(self.option("U14")["team_name"], self.other_team.name)  # a variant's own team wins
        self.assertIsNone(self.option("Volunteer")["team_id"])  # no team: the registrant may ask for one

    def test_the_team_is_fixed_whatever_the_form_sends(self):
        batch = self.batch(entries=[self.entry(requested_team_id=str(self.other_team.pk))])  # the product's team is self.team
        self.assertEqual(batch.details.get().requested_team, self.team)
        batch = self.batch(entries=[self.entry("Two", variant_id=str(self.u14.pk))])  # nothing sent: the variant's team
        self.assertEqual(batch.details.get().requested_team, self.other_team)
        batch = self.batch(entries=[self.entry("Three", variant_id=str(self.free_variant.pk), requested_team_id=str(self.other_team.pk))])  # no fixed team: the request stands
        self.assertEqual(batch.details.get().requested_team, self.other_team)
        self.assertEqual(self.register([self.entry("Four", variant_id=str(self.free_variant.pk), requested_team_id="00000000-0000-0000-0000-000000000000")]).status_code, 422)  # unknown team still refused when free
        self.assertEqual(self.register([self.entry("Five", requested_team_id="00000000-0000-0000-0000-000000000000")]).status_code, 201)  # but ignored when fixed

    def test_the_jersey_number_is_checked_for_the_fixed_team(self):
        r = self.register([self.entry(requested_team_id=str(self.other_team.pk), requested_jersey_number=30)])  # 30 is outside the fixed team's pool (1 to 5)
        self.assertEqual(r.status_code, 422)
        self.assertIn("entries.0.requested_jersey_number", r.json()["errors"])
        self.assertEqual(self.register([self.entry(requested_team_id=str(self.other_team.pk), requested_jersey_number=3)]).status_code, 201)

    def test_the_numbers_endpoint_uses_the_fixed_team(self):
        r = self.public("get", f"/numbers?team_id={self.other_team.pk}&option_id=variant:{self.variant.pk}&date_of_birth=2015-05-05")  # asked for another team, the option's team wins
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["pool"]["name"], r.json()["numbers"]), ("Juniors", [1, 2, 3, 4, 5]))

    def test_volunteers_are_not_placed_in_a_team(self):
        self.product.category = ProductCategory.objects.get(system_key="volunteer")
        self.product.team = None
        self.product.save()
        batch = self.batch(entries=[self.entry(kind="volunteer", variant_id=str(self.variant.pk))])
        self.assertEqual(batch.details.get().requested_team, None)

    def test_staff_can_change_the_team_while_reviewing(self):
        batch = self.batch(entries=[self.entry()])
        line = batch.details.get()
        self.assertEqual(self.staff("get", f"/registrations/{batch.pk}").json()["lines"][0]["requested_team_id"], str(self.team.pk))
        r = self.staff("patch", f"/registrations/{batch.pk}", {"lines": [{"id": str(line.pk), "requested_team_id": str(self.other_team.pk)}]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["lines"][0]["requested_team_name"], self.other_team.name)
        unknown = self.staff("patch", f"/registrations/{batch.pk}", {"lines": [{"id": str(line.pk), "requested_team_id": "00000000-0000-0000-0000-000000000000"}]})
        self.assertEqual(unknown.status_code, 422)
        self.assertEqual(self.staff("patch", f"/registrations/{batch.pk}", {"lines": [{"id": str(line.pk), "excluded": False}]}).json()["lines"][0]["requested_team_name"], self.other_team.name)  # other edits keep it
        self.assertEqual(self.staff("post", f"/registrations/{batch.pk}/confirm", {"send": False}).status_code, 200)
        self.assertTrue(TeamMembership.objects.filter(member=line.member, team=self.other_team, season=self.season).exists())  # placed where staff said
        cleared_batch = self.batch(entries=[self.entry("Two")])
        two = cleared_batch.details.get()
        self.assertIsNone(self.staff("patch", f"/registrations/{cleared_batch.pk}", {"lines": [{"id": str(two.pk), "requested_team_id": None}]}).json()["lines"][0]["requested_team_id"])

    def test_staff_cannot_put_a_volunteer_in_a_team(self):
        self.free.category = ProductCategory.objects.get(system_key="volunteer")
        self.free.save()
        batch = self.batch(entries=[self.entry(kind="volunteer", variant_id=str(self.free_variant.pk))])
        line = batch.details.get()
        r = self.staff("patch", f"/registrations/{batch.pk}", {"lines": [{"id": str(line.pk), "requested_team_id": str(self.team.pk)}]})
        self.assertEqual(r.status_code, 422)
        self.assertIn("lines.0.requested_team_id", r.json()["errors"])
