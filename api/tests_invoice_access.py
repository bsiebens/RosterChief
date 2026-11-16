"""Member-side invoice download: only the registration contact or a parent/guardian of the family of the people the invoice is for."""

import datetime
from unittest import mock

from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, ensure_member_for_user
from controlpanel.tests import PASSWORD, make_user
from events.models import Season
from members.models import ClubMembership, Family, FamilyMembership, Member
from members.services import invoice_access
from registration.models import RegistrationBatch, RegistrationDetails

Role = FamilyMembership.FamilyRole


class InvoiceAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = make_user("owner@invoice.test")
        platform = Club(schema_name="public", name="Platform", owner=owner)
        platform.auto_create_schema = False
        platform.save()
        Domain.objects.create(domain="testserver", tenant=platform, is_primary=True)
        cls.club = create_club("Invoice FC", owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        cls.users = {}
        cls.members = {}
        for name in ("contact", "parent", "teen", "uncle", "stranger"):
            user = make_user(f"{name}@invoice.test")
            cls.club.add_user(user)
            cls.users[name] = user
            cls.members[name] = ensure_member_for_user(cls.club, user)
        with schema_context(cls.club.schema_name):
            cls.child = Member.objects.create(first_name="Kid", last_name="Player")
            family = Family.objects.create(name="Family")
            for name, role in (("parent", Role.PARENT), ("teen", Role.CHILD), ("uncle", Role.OTHER)):
                FamilyMembership.objects.create(family=family, member=cls.members[name], role=role)
            FamilyMembership.objects.create(family=family, member=cls.child, role=Role.CHILD)
            today = timezone.localdate()
            season = Season.objects.create(start_date=today - datetime.timedelta(days=10), end_date=today + datetime.timedelta(days=300))
            membership = ClubMembership.objects.create(member=cls.child, season=season)
            cls.batch = RegistrationBatch.objects.create(
                contact=cls.members["contact"], season=season, confirmed_at=timezone.now(), invoice_number="INV-1", invoice_due_date=today,
            )
            RegistrationDetails.objects.create(batch=cls.batch, membership=membership, member=cls.child)

    def tearDown(self):
        connection.set_schema_to_public()

    def get(self, name):
        login = Client().post("/_allauth/app/v1/auth/login", data={"email": self.users[name].email, "password": PASSWORD}, content_type="application/json", HTTP_HOST=self.host)
        token = login.json()["meta"]["session_token"]
        with mock.patch("registration.services.notifications.render_invoice_pdf", return_value=b"%PDF-1.4"):
            return Client().get(f"/api/v1/portal/invoices/{self.batch.pk}.pdf", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=token)

    def test_contact_and_parent_of_the_family_may_download(self):
        self.assertEqual(self.get("contact").status_code, 200)
        self.assertEqual(self.get("parent").status_code, 200)

    def test_profile_overview_links_the_invoice_for_a_parent_who_is_not_the_contact(self):
        login = Client().post("/_allauth/app/v1/auth/login", data={"email": self.users["parent"].email, "password": PASSWORD}, content_type="application/json", HTTP_HOST=self.host)
        token = login.json()["meta"]["session_token"]
        rows = Client().get("/api/v1/portal/me/overview", HTTP_HOST=self.host, HTTP_X_SESSION_TOKEN=token).json()["memberships"]
        self.assertEqual([(row["member_id"], row["invoice_id"]) for row in rows], [(str(self.child.pk), str(self.batch.pk))])

    def test_child_other_adult_and_stranger_get_404(self):
        for name in ("teen", "uncle", "stranger"):
            self.assertEqual(self.get(name).status_code, 404, name)

    def test_helper_and_home_list(self):
        with schema_context(self.club.schema_name):
            self.assertTrue(invoice_access.can_download(self.members["parent"], self.batch))
            self.assertFalse(invoice_access.can_download(self.members["teen"], self.batch))
            self.assertEqual([b.pk for b in invoice_access.downloadable_invoices(self.members["contact"])], [self.batch.pk])
            RegistrationBatch.objects.filter(pk=self.batch.pk).update(cancelled_at=timezone.now())
            self.assertFalse(invoice_access.can_download(self.members["parent"], self.batch))
