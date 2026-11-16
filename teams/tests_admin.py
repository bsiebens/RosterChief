"""Smoke test: every tenant admin changelist and add page renders for a club owner on a club domain."""

from unittest import mock

from django.apps import apps
from django.contrib import admin
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club
from controlpanel.tests import login_session, make_user


@override_settings(DEBUG=True)
class TenantAdminSmokeTests(TestCase):
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

    def test_changelist_and_add_pages(self):
        client = Client(HTTP_HOST=self.host)
        client.cookies["sessionid"] = login_session(self.owner)
        checked = 0
        with schema_context(self.club.schema_name):
            for app in ("teams", "events", "sponsoring"):
                for model in apps.get_app_config(app).get_models():
                    self.assertTrue(admin.site.is_registered(model), model._meta.label)
                    name = f"admin:{app}_{model._meta.model_name}"
                    for suffix in ("changelist", "add"):
                        r = client.get(reverse(f"{name}_{suffix}"))
                        self.assertEqual(r.status_code, 200, f"{name}_{suffix}")
                        checked += 1
        self.assertGreater(checked, 20)
