"""Test runner that makes every test club a clone of one ready-migrated schema.

Creating a club runs every tenant migration (several seconds), and nearly every test class creates its own club. This runner migrates a
``template`` schema once per test database, then turns on django-tenants' base-schema cloning (``TENANT_CREATION_FAKES_MIGRATIONS`` +
``TENANT_BASE_SCHEMA``), so a new club is a fast SQL clone plus the usual ``post_schema_sync`` seeding. Each class still gets its own
fresh, isolated schema. Production club creation is unchanged. Turn it off with ``ROSTERCHIEF_TEST_TEMPLATE=0`` to compare.

The runner also uses a fast password hasher and empties the in-memory cache before each test. These settings are applied in the main process
only, so ``--parallel`` falls back to slow club creation and is slower overall: run the suite sequentially.
"""

import os

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.db import connections
from django.test import SimpleTestCase, override_settings
from django.test.runner import DiscoverRunner
from django_tenants.utils import get_tenant_database_alias

TEMPLATE_SCHEMA = "template"


class TemplateSchemaRunner(DiscoverRunner):
    _template_settings = None
    _original_pre_setup = None
    _fast_hasher = None

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        # Every test user and every test sign-in would otherwise pay for a full-strength PBKDF2 hash (about 0.1s each, thousands of times).
        self._fast_hasher = override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
        self._fast_hasher.enable()
        # A fast suite makes many sign-ins from one address inside a minute and trips rate limits that live in the cache, which the slow
        # suite never reached. Every test gets an empty in-memory cache (never a real Redis, which may be a developer's own).
        if "locmem" in settings.CACHES["default"]["BACKEND"].lower():
            original = SimpleTestCase.__dict__["_pre_setup"]  # a classmethod: Django runs it before every test
            self._original_pre_setup = original

            def pre_setup(cls):
                cache.clear()
                original.__func__(cls)

            SimpleTestCase._pre_setup = classmethod(pre_setup)

    def teardown_test_environment(self, **kwargs):
        if self._fast_hasher is not None:
            self._fast_hasher.disable()
            self._fast_hasher = None
        if self._original_pre_setup is not None:
            SimpleTestCase._pre_setup = self._original_pre_setup
            self._original_pre_setup = None
        super().teardown_test_environment(**kwargs)

    def setup_databases(self, **kwargs):
        config = super().setup_databases(**kwargs)
        if os.environ.get("ROSTERCHIEF_TEST_TEMPLATE", "1") != "0":
            self._build_template()
        return config

    def teardown_databases(self, old_config, **kwargs):
        if self._template_settings is not None:
            self._template_settings.disable()
            self._template_settings = None
        super().teardown_databases(old_config, **kwargs)

    def _build_template(self):
        connection = connections[get_tenant_database_alias()]
        with connection.cursor() as cursor:
            # A kept test database (--keepdb) may hold a template from older migrations: always rebuild it.
            cursor.execute(f"DROP SCHEMA IF EXISTS {TEMPLATE_SCHEMA} CASCADE")
            cursor.execute(f"CREATE SCHEMA {TEMPLATE_SCHEMA}")
        call_command("migrate_schemas", schema_name=TEMPLATE_SCHEMA, interactive=False, verbosity=0)
        connection.set_schema_to_public()
        self._template_settings = override_settings(TENANT_CREATION_FAKES_MIGRATIONS=True, TENANT_BASE_SCHEMA=TEMPLATE_SCHEMA)
        self._template_settings.enable()
