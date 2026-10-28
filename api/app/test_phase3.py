"""Phase 3 of the app API: payments, forms, photo upload, bug reports, calendar feed.
Same fixtures as test_member (a parent Pat with a child Kim, signed in)."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from api.models import IdempotencyRecord
from bugs.models import BugNote, BugReport
from club.models import ClubMembership
from formbuilder.models import Field as FormField
from formbuilder.models import Form, FormSend, Submission
from mobile.models import CalendarFeedToken
from registration.models import RegistrationBatch, RegistrationDetails

from .test_member import ClubApiTestCase, png_bytes
from .tests import CLUB_HOST


class PaymentsTests(ClubApiTestCase):
    def confirmed_batch(self, **kwargs):
        number = RegistrationBatch.objects.count() + 1
        defaults = {
            "club": self.club,
            "season": self.season,
            "contact_first_name": "Pat",
            "contact_last_name": "Parent",
            "contact_email": "parent@example.com",
            "invoice_number": f"REG-{number}",
            "invoice_sent_at": timezone.now(),
            "invoice_due_date": timezone.localdate() + datetime.timedelta(days=14),
        }
        defaults.update(kwargs)
        return RegistrationBatch.objects.create(**defaults)

    def test_registration_groups_the_family(self):
        membership = ClubMembership.objects.get(member=self.child)
        ClubMembership.objects.filter(pk=membership.pk).update(fee_amount=Decimal("150.00"))
        batch = self.confirmed_batch()
        RegistrationDetails.objects.create(membership=membership, batch=batch, price=Decimal("150.00"))
        self.club.payment_instructions = "BE00 0000 0000 0000"
        self.club.save()

        body = self.get("/payments").json()

        self.assertEqual(body["payment_instructions"], "BE00 0000 0000 0000")
        item = body["items"][0]
        self.assertEqual((item["kind"], item["registration_id"]), ("registration", str(batch.pk)))
        self.assertEqual(item["total_balance"], {"amount": "150.00", "currency": "EUR"})
        self.assertEqual(item["members"][0]["member_id"], str(self.child.pk))
        self.assertEqual(item["members"][0]["lines"][0]["price"], {"amount": "150.00", "currency": "EUR"})

    def test_unconfirmed_registration_is_held_back(self):
        membership = ClubMembership.objects.get(member=self.child)
        ClubMembership.objects.filter(pk=membership.pk).update(fee_amount=Decimal("150.00"))
        RegistrationDetails.objects.create(membership=membership, batch=self.confirmed_batch(invoice_sent_at=None), price=Decimal("150.00"))
        self.assertEqual(self.get("/payments").json()["items"], [])

    def test_invoice_pdf(self):
        membership = ClubMembership.objects.get(member=self.child)
        batch = self.confirmed_batch()
        RegistrationDetails.objects.create(membership=membership, batch=batch, price=Decimal("150.00"))
        with patch("api.app.routers.payments.batch_invoice_pdf", return_value=b"%PDF-fake"):
            response = self.get(f"/payments/registrations/{batch.pk}/invoice.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response.content, b"%PDF-fake")

    def test_someone_elses_or_unconfirmed_invoice_is_404(self):
        stranger_membership = ClubMembership.objects.get(member=self.stranger())
        theirs = self.confirmed_batch()
        RegistrationDetails.objects.create(membership=stranger_membership, batch=theirs, price=Decimal("1"))
        unconfirmed = self.confirmed_batch(invoice_sent_at=None)
        RegistrationDetails.objects.create(membership=ClubMembership.objects.get(member=self.child), batch=unconfirmed, price=Decimal("1"))

        self.assertError(self.get(f"/payments/registrations/{theirs.pk}/invoice.pdf"), 404, "not_found")
        self.assertError(self.get(f"/payments/registrations/{unconfirmed.pk}/invoice.pdf"), 404, "not_found")


class FormsTests(ClubApiTestCase):
    def setUp(self):
        super().setUp()
        self.form = Form.objects.create(club=self.club, title="Medical info")
        FormField.objects.create(form=self.form, key="allergies", label="Allergies", field_type=FormField.FieldType.TEXT, required=True, order=1)
        FormField.objects.create(form=self.form, key="size", label="Shirt size", field_type=FormField.FieldType.CHOICE, options=["S", "M"], required=False, order=2)
        FormField.objects.create(form=self.form, key="document", label="Certificate", field_type=FormField.FieldType.FILE, required=False, order=3)
        self.send = FormSend.objects.create(club=self.club, form=self.form)
        self.send.invited_members.add(self.child)

    def submit(self, data, **headers):
        return self.api("post", f"/forms/{self.send.pk}/submit", token=self.token, host=CLUB_HOST, data=data, **headers)

    def test_list_and_schema(self):
        rows = self.get("/forms").json()["items"]
        self.assertEqual([(row["title"], row["member_id"], row["submitted_at"]) for row in rows], [("Medical info", str(self.child.pk), None)])

        body = self.get(f"/forms/{self.send.pk}").json()
        self.assertEqual([field["key"] for field in body["fields"]], ["allergies", "size", "document"])
        self.assertEqual(body["fields"][1]["options"], [{"value": "S", "label": "S"}, {"value": "M", "label": "M"}])
        self.assertEqual(body["addressed"], [{"member_id": str(self.child.pk), "submissions": 0}])
        self.assertTrue(body["is_open"])

    def test_json_submit_for_a_child_and_read_it_back(self):
        response = self.submit({"member_id": str(self.child.pk), "answers": {"allergies": "Peanuts", "size": "M"}}, HTTP_IDEMPOTENCY_KEY="k-1")

        self.assertEqual(response.status_code, 201, response.content)
        submission = Submission.objects.get()
        self.assertEqual((submission.member, submission.submitted_by), (self.child, self.member))
        answers = {answer["key"]: answer["value"] for answer in self.get(f"/forms/submissions/{submission.pk}").json()["answers"]}
        self.assertEqual(answers, {"allergies": "Peanuts", "size": "M", "document": None})

        replay = self.submit({"member_id": str(self.child.pk), "answers": {"allergies": "Peanuts", "size": "M"}}, HTTP_IDEMPOTENCY_KEY="k-1")
        self.assertEqual(replay.json()["id"], response.json()["id"])
        self.assertEqual(Submission.objects.count(), 1)

    def test_multipart_submit_with_a_file(self):
        upload = SimpleUploadedFile("cert.pdf", b"%PDF-1.4 fake", content_type="application/pdf")
        response = self.client.post(f"/api/app/v1/forms/{self.send.pk}/submit", {"member_id": str(self.child.pk), "allergies": "None", "document": upload}, HTTP_HOST=CLUB_HOST, HTTP_AUTHORIZATION=f"Bearer {self.token}")

        self.assertEqual(response.status_code, 201, response.content)
        document = next(answer for answer in response.json()["answers"] if answer["key"] == "document")
        self.assertEqual(document["value"]["name"], "cert.pdf")

    def test_validation_and_closed(self):
        response = self.submit({"member_id": str(self.child.pk), "answers": {"size": "XXL"}})
        self.assertError(response, 422, "validation_error")
        self.assertEqual(set(response.json()["error"]["fields"]), {"allergies", "size"})
        self.assertFalse(IdempotencyRecord.objects.exists())

        FormSend.objects.filter(pk=self.send.pk).update(closes_at=timezone.now() - datetime.timedelta(days=1))
        self.assertError(self.submit({"member_id": str(self.child.pk), "answers": {"allergies": "x"}}), 409, "form_unavailable")

    def test_not_addressed(self):
        self.assertError(self.submit({"member_id": str(self.member.pk), "answers": {"allergies": "x"}}), 404, "not_found")
        other = FormSend.objects.create(club=self.club, form=self.form)
        other.invited_members.add(self.stranger())
        self.assertError(self.get(f"/forms/{other.pk}"), 404, "not_found")


class PhotoUploadTests(ClubApiTestCase):
    def upload(self, **data):
        return self.client.post(f"/api/app/v1/members/{self.child.pk}/photo", data, HTTP_HOST=CLUB_HOST, HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def test_upload_sets_the_profile_picture_and_delete_clears_it(self):
        response = self.upload(photo=SimpleUploadedFile("kim.png", png_bytes(), content_type="image/png"), photo_public_consent="true")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIsNotNone(body["photo_url"])
        self.assertIsNotNone(body["profile_photo_url"])
        self.assertIsNone(body["player_photo_url"])
        self.assertTrue(body["photo_public_consent"])
        self.child.refresh_from_db()
        self.assertTrue(self.child.profile_photo)
        self.assertFalse(self.child.player_photo)

        removed = self.send("delete", f"/members/{self.child.pk}/photo")
        self.assertIsNone(removed.json()["photo_url"])
        self.assertIsNone(removed.json()["profile_photo_url"])
        self.assertTrue(removed.json()["photo_public_consent"])

    def test_the_player_picture_is_untouched_by_upload_and_delete(self):
        self.child.player_photo = SimpleUploadedFile("official.png", png_bytes(), content_type="image/png")
        self.child.save()
        official = self.child.player_photo.name

        self.upload(photo=SimpleUploadedFile("kim.png", png_bytes(), content_type="image/png"))
        self.send("delete", f"/members/{self.child.pk}/photo")
        # A player_photo part is ignored: only the profile picture is settable here.
        self.upload(player_photo=SimpleUploadedFile("x.png", png_bytes(), content_type="image/png"))

        self.child.refresh_from_db()
        self.assertEqual(self.child.player_photo.name, official)
        self.assertFalse(self.child.profile_photo)

    def test_not_an_image(self):
        response = self.upload(photo=SimpleUploadedFile("kim.png", b"not an image", content_type="image/png"))
        self.assertError(response, 422, "validation_error")
        self.assertIn("photo", response.json()["error"]["fields"])

    def test_missing_file(self):
        self.assertError(self.upload(), 422, "validation_error")


class SupportTests(ClubApiTestCase):
    def test_file_and_list_bug_reports(self):
        created = self.send("post", "/bug-reports", {"title": "Crash", "description": "On the calendar"})
        self.assertEqual(created.status_code, 201)
        bug = BugReport.objects.get()
        self.assertEqual(bug.reported_by, self.member)
        BugNote.objects.create(bug=bug, body="Looking into it")

        items = self.get("/bug-reports").json()["items"]
        self.assertEqual([(item["title"], item["status"]) for item in items], [("Crash", "submitted")])
        self.assertEqual(items[0]["notes"][0]["body"], "Looking into it")
        self.assertNotIn("priority", items[0])

    def test_calendar_feed_link(self):
        self.assertEqual(self.get("/calendar-feed").json(), {"has_feed": False})

        first = self.send("post", "/calendar-feed").json()
        second = self.send("post", "/calendar-feed").json()

        self.assertTrue(first["webcal_url"].startswith("webcal://ajax-united.rosterchief.app/app/calendar/"))
        self.assertNotEqual(first["feed_url"], second["feed_url"])
        self.assertEqual(CalendarFeedToken.objects.count(), 1)
        feed = self.client.get(second["feed_url"].replace("http://ajax-united.rosterchief.app", ""), HTTP_HOST=CLUB_HOST)
        self.assertEqual(feed.status_code, 200)
        old = self.client.get(first["feed_url"].replace("http://ajax-united.rosterchief.app", ""), HTTP_HOST=CLUB_HOST)
        self.assertEqual(old.status_code, 404)


class ShopTests(ClubApiTestCase):
    def setUp(self):
        super().setUp()
        from waffle import get_waffle_flag_model

        from shop.models import Product, ProductVariant

        # The "order placed" email runs on a background thread; not what these tests are about.
        background = patch("shop.services.notifications.run_in_background")
        background.start()
        self.addCleanup(background.stop)
        get_waffle_flag_model().objects.get_or_create(name="shop")[0].clubs.add(self.club)
        type(self.club).objects.filter(pk=self.club.pk).update(shop_open=True)
        self.jersey = Product.objects.create(club=self.club, name="Home Jersey", slug="home-jersey", price=Decimal("25.00"), personalization_enabled=True)
        self.medium = ProductVariant.objects.create(product=self.jersey, name="M", price=Decimal("27.50"))
        Product.objects.create(club=self.club, name="Season fee", slug="season-fee", price=Decimal("150"), product_type=Product.ProductType.MEMBERSHIP)

    def add(self, **data):
        return self.send("post", "/shop/cart/items", {"product_id": str(self.jersey.pk), **data})

    def test_catalogue_leaves_out_membership_products(self):
        items = self.get("/shop/products").json()["items"]
        self.assertEqual([item["slug"] for item in items], ["home-jersey"])
        self.assertEqual(items[0]["variants"], [{"id": str(self.medium.pk), "name": "M", "price": {"amount": "27.50", "currency": "EUR"}, "in_stock": True}])
        self.assertError(self.get("/shop/products/season-fee"), 404, "not_found")

    # --- stock flags: product.in_stock follows its options, variant.in_stock stays per option ---

    def stocked(self, name, *, options=(), managed=True, stock=None):
        """A product (and active options) with stock given as ``{option name or None: units}``."""
        from shop.models import Product, ProductVariant
        from shop.services.inventory import receive_stock

        product = Product.objects.create(club=self.club, name=name, slug=name.lower().replace(" ", "-"), price=Decimal("10.00"), inventory_managed=managed)
        variants = {option: ProductVariant.objects.create(product=product, name=option) for option in options}
        for option, units in (stock or {}).items():
            if units:
                receive_stock(self.club, {(product, variants.get(option)): units})
        return product, variants

    def flags(self, product):
        data = self.get(f"/shop/products/{product.slug}").json()
        return data["in_stock"], {variant["name"]: variant["in_stock"] for variant in data["variants"]}

    def test_one_sold_out_option_leaves_the_product_in_stock(self):
        product, _ = self.stocked("Scarf", options=("S", "M", "L"), stock={"M": 4, "L": 1})

        self.assertEqual(self.flags(product), (True, {"S": False, "M": True, "L": True}))

    def test_a_product_is_sold_out_only_when_every_option_is(self):
        product, _ = self.stocked("Hat", options=("S", "M"), stock={})

        self.assertEqual(self.flags(product), (False, {"S": False, "M": False}))

    def test_a_product_without_options_follows_its_own_stock(self):
        sold_out, _ = self.stocked("Mug", stock={})
        available, _ = self.stocked("Cup", stock={None: 2})

        self.assertEqual(self.flags(sold_out), (False, {}))
        self.assertEqual(self.flags(available), (True, {}))

    def test_an_unmanaged_product_is_always_in_stock(self):
        product, _ = self.stocked("Flag", options=("S", "M"), managed=False)

        self.assertEqual(self.flags(product), (True, {"S": True, "M": True}))

    def test_inactive_options_do_not_affect_the_product_flag(self):
        product, variants = self.stocked("Sock", options=("S", "M"), stock={"M": 3})
        variants["M"].is_active = False
        variants["M"].save()

        # Only "S" is left (sold out): the stock on the withdrawn "M" doesn't count.
        self.assertEqual(self.flags(product), (False, {"S": False}))

    def test_the_catalogue_flags_match_the_product_endpoint(self):
        product, _ = self.stocked("Scarf", options=("S", "M"), stock={"M": 4})

        item = next(item for item in self.get("/shop/products").json()["items"] if item["slug"] == product.slug)

        self.assertEqual((item["in_stock"], {variant["name"]: variant["in_stock"] for variant in item["variants"]}), (True, {"S": False, "M": True}))

    def test_an_in_stock_option_can_be_ordered_while_another_is_sold_out(self):
        product, variants = self.stocked("Scarf", options=("S", "M"), stock={"M": 4})

        sold_out = self.send("post", "/shop/cart/items", {"product_id": str(product.pk), "variant_id": str(variants["S"].pk)})
        self.assertError(sold_out, 409, "out_of_stock")

        added = self.send("post", "/shop/cart/items", {"product_id": str(product.pk), "variant_id": str(variants["M"].pk)})
        self.assertEqual(added.status_code, 201, added.content)

    def test_only_the_sold_out_cart_line_is_unavailable(self):
        product, variants = self.stocked("Scarf", options=("S", "M"), stock={"S": 2, "M": 2})
        for option in ("S", "M"):
            self.send("post", "/shop/cart/items", {"product_id": str(product.pk), "variant_id": str(variants[option].pk)})
        from shop.services.inventory import set_stock

        set_stock(self.club, product, variants["S"], 0)

        lines = {line["variant"]["name"]: line for line in self.get("/shop/cart").json()["items"]}

        self.assertEqual({name: line["unavailable"] for name, line in lines.items()}, {"S": True, "M": False})
        self.assertEqual({name: line["variant"]["in_stock"] for name, line in lines.items()}, {"S": False, "M": True})

    def test_cart_and_checkout(self):
        added = self.add(variant_id=str(self.medium.pk), quantity=2, beneficiary_id=str(self.child.pk), personalization_number="7")
        self.assertEqual(added.status_code, 201, added.content)
        line = added.json()["items"][0]
        self.assertEqual((line["quantity"], line["line_total"]["amount"], line["beneficiary_id"], line["personalization_number"]), (2, "55.00", str(self.child.pk), "7"))

        self.assertEqual(self.send("patch", f"/shop/cart/items/{line['id']}", {"quantity": 1}).json()["total"]["amount"], "27.50")

        order = self.send(
            "post",
            "/shop/orders",
            {},
        ).json()
        self.assertEqual((order["total"]["amount"], order["payment_status"], order["status"]), ("27.50", "pending", "not_ready"))
        self.assertEqual(order["lines"][0]["variant_name"], "M")
        self.assertEqual(self.get("/shop/cart").json()["items"], [])
        self.assertEqual([item["number"] for item in self.get("/shop/orders").json()["items"]], [order["number"]])

    def test_checkout_is_idempotent(self):
        self.add()
        first = self.api("post", "/shop/orders", token=self.token, host=CLUB_HOST, data={}, HTTP_IDEMPOTENCY_KEY="tap-1")
        second = self.api("post", "/shop/orders", token=self.token, host=CLUB_HOST, data={}, HTTP_IDEMPOTENCY_KEY="tap-1")
        self.assertEqual(first.json()["id"], second.json()["id"])

    def test_empty_cart_and_bad_input(self):
        self.assertError(self.send("post", "/shop/orders", {}), 409, "checkout_failed")
        self.assertError(self.add(variant_id=str(self.jersey.pk)), 422, "validation_error")
        self.assertError(self.add(beneficiary_id=str(self.stranger().pk)), 404, "not_found")

    def test_closed_and_unlicensed(self):
        self.add()
        order = self.send("post", "/shop/orders", {}).json()
        type(self.club).objects.filter(pk=self.club.pk).update(shop_open=False)

        self.assertError(self.get("/shop/products"), 409, "shop_closed")
        self.assertError(self.get("/shop/cart"), 409, "shop_closed")
        self.assertEqual(self.get(f"/shop/orders/{order['id']}").status_code, 200)

        from waffle import get_waffle_flag_model

        get_waffle_flag_model().objects.get(name="shop").clubs.remove(self.club)
        self.assertError(self.get("/shop/orders"), 404, "feature_disabled")

    def test_someone_elses_order_is_404(self):
        from shop.models import Order

        theirs = Order.objects.create(club=self.club, purchaser=self.stranger(), total=Decimal("5"))
        self.assertError(self.get(f"/shop/orders/{theirs.pk}"), 404, "not_found")


class RegistrationTests(ClubApiTestCase):
    def setUp(self):
        super().setUp()
        from shop.models import Product, ProductVariant
        from teams.models import Team

        self.product = Product.objects.create(club=self.club, name="Player Registration", product_type=Product.ProductType.MEMBERSHIP, season=self.season, price=Decimal("100.00"))
        self.u10 = ProductVariant.objects.create(product=self.product, name="U10", price=Decimal("80.00"))
        self.team = Team.objects.create(club=self.club, name="U10 Boys", short_name="U10B")

    def payload(self, *entries):
        return {"season_id": str(self.season.pk), "entries": list(entries)}

    def entry(self, **kwargs):
        return {"variant_id": str(self.u10.pk), **kwargs}

    def test_options(self):
        body = self.get("/registration/options").json()

        self.assertEqual(body["season"]["id"], str(self.season.pk))
        self.assertEqual(body["products"][0]["variants"], [{"id": str(self.u10.pk), "name": "U10", "price": {"amount": "80.00", "currency": "EUR"}}])
        self.assertEqual(body["teams"], [{"id": str(self.team.pk), "name": "U10 Boys", "short_name": "U10B", "jersey_numbers": None}])

    def test_quote_saves_nothing(self):
        from registration.models import RegistrationBatch

        body = self.send("post", "/registration/quote", self.payload(self.entry(member_id=str(self.child.pk)), self.entry(first_name="Lou", last_name="Parent", date_of_birth="2019-02-03"))).json()

        self.assertEqual([(line["first_name"], line["price"]["amount"]) for line in body["lines"]], [("Kim", "80.00"), ("Lou", "80.00")])
        self.assertEqual(body["total"], {"amount": "160.00", "currency": "EUR"})
        self.assertFalse(RegistrationBatch.objects.exists())

    def test_submit_registers_each_person_as_themselves(self):
        from django.core import mail

        from registration.models import RegistrationDetails

        payload = self.payload(self.entry(member_id=str(self.member.pk)), self.entry(member_id=str(self.child.pk), team_id=str(self.team.pk)))
        first = self.api("post", "/registration", token=self.token, host=CLUB_HOST, data=payload, HTTP_IDEMPOTENCY_KEY="reg-1")
        again = self.api("post", "/registration", token=self.token, host=CLUB_HOST, data=payload, HTTP_IDEMPOTENCY_KEY="reg-1")

        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(first.json(), again.json())
        self.assertEqual(sorted(RegistrationDetails.objects.values_list("membership__member__first_name", flat=True)), ["Kim", "Pat"])
        self.assertEqual(RegistrationDetails.objects.get(membership__member=self.child).requested_team, self.team)
        self.assertEqual(len(mail.outbox), 1)

    def test_validation(self):
        from shop.models import Product, ProductVariant

        other_season = type(self.season).objects.create(club=self.club, start_date=self.season.end_date + datetime.timedelta(days=1), end_date=self.season.end_date + datetime.timedelta(days=300))
        later = ProductVariant.objects.create(product=Product.objects.create(club=self.club, name="Next", product_type=Product.ProductType.MEMBERSHIP, season=other_season, price=Decimal("1")), name="All")

        wrong_variant = self.send("post", "/registration/quote", self.payload(self.entry(member_id=str(self.child.pk), variant_id=str(later.pk))))
        self.assertError(wrong_variant, 422, "validation_error")
        self.assertIn("entries.0.variant_id", wrong_variant.json()["error"]["fields"])

        no_name = self.send("post", "/registration/quote", self.payload(self.entry()))
        self.assertIn("entries.0.last_name", no_name.json()["error"]["fields"])

        self.assertError(self.send("post", "/registration/quote", self.payload(self.entry(member_id=str(self.stranger().pk)))), 404, "not_found")
        bad_season = self.send("post", "/registration/quote", {"season_id": "00000000-0000-0000-0000-000000000000", "entries": [self.entry(member_id=str(self.child.pk))]})
        self.assertIn("season_id", bad_season.json()["error"]["fields"])

    def test_closed_registration(self):
        self.product.is_active = False
        self.product.save()
        body = self.get("/registration/options").json()
        self.assertEqual((body["seasons"], body["season"], body["products"]), ([], None, []))
