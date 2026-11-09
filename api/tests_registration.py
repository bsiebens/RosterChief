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
from teams.models import Position, TeamMembership

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
        batch = self.batch(entries=[self.entry(requested_team_id=str(self.team.pk), requested_jersey_number=7), self.entry("Two", requested_team_id=str(self.team.pk), requested_jersey_number=7)])
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
