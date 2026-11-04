"""Smoke test: every members admin page renders on a club domain."""

import datetime
from unittest import mock

from django.apps import apps
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club
from controlpanel.tests import login_session, make_user
from events.models import Season
from members.models import AuditEntry, DuesInvoice, FeePayment, Member, MemberRequirementStatus, OnboardingRequirement
from members.services import families, memberships


@override_settings(DEBUG=True)
class MembersAdminSmokeTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._mfa = mock.patch("authorization.middleware.is_local_development", return_value=True)
        cls._mfa.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()
        cls._mfa.stop()

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@admin.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Admin FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.member = Member.objects.create(first_name="Ann", last_name="Admin", email="ann@admin.test")
            kid = Member.objects.create(first_name="Kid", last_name="Admin")
            families.create_family(cls.owner, members=[(cls.member, "parent"), (kid, "child")])
            cm = memberships.create_membership(cls.owner, cls.member, season)
            FeePayment.objects.create(membership=cm, amount=5)
            DuesInvoice.objects.create(membership=cm, amount=10, due_date=today)
            req = OnboardingRequirement.objects.create(name="Photo")
            MemberRequirementStatus.objects.get_or_create(membership=cm, requirement=req)

    def test_all_pages_render(self):
        client = Client(HTTP_HOST=self.host)
        client.cookies["sessionid"] = login_session(self.owner)
        with schema_context(self.club.schema_name):
            for model in apps.get_app_config("members").get_models():
                name = f"admin:members_{model._meta.model_name}"
                urls = [reverse(f"{name}_changelist")]
                if model is not AuditEntry:
                    urls.append(reverse(f"{name}_add"))
                for obj in model.objects.all()[:1]:
                    urls.append(reverse(f"{name}_change", args=[obj.pk]))
                for url in urls:
                    r = client.get(url)
                    self.assertEqual(r.status_code, 200, (url, r.status_code))
            self.assertEqual(client.get(reverse("admin:members_auditentry_add")).status_code, 403)
