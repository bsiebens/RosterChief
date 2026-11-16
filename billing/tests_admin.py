"""Smoke test: every registered billing and controlpanel admin page renders on the base domain."""

from unittest import mock

from django.contrib import admin
from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse

from club.models import Club, Domain
from controlpanel.tests import login_session, make_user

APPS = {"billing", "controlpanel", "features"}


class AdminSmokeTests(TestCase):
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
        cls.user = make_user("admin@platform.test", level="superuser")
        public = Club(schema_name="public", name="Platform", owner=cls.user)
        public.auto_create_schema = False
        public.save()
        cls.user.tenants.add(public)
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)

    def test_pages_render(self):
        client = Client()
        client.cookies["sessionid"] = login_session(self.user)
        checked = 0
        for model, model_admin in admin.site._registry.items():
            if model._meta.app_label not in APPS:
                continue
            request = mock.Mock(user=self.user)
            info = (model._meta.app_label, model._meta.model_name)
            with self.subTest(model=model.__name__):
                self.assertEqual(client.get(reverse("admin:{}_{}_changelist".format(*info))).status_code, 200)
                if model_admin.has_add_permission(request):
                    self.assertEqual(client.get(reverse("admin:{}_{}_add".format(*info))).status_code, 200)
                checked += 1
        self.assertGreaterEqual(checked, 17)
