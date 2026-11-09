import importlib

from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from news.tests import NewsClubMixin


class MovePermissionsTests(NewsClubMixin, TestCase):
    """Roles that held the evaluation permissions under ``members`` keep them under ``evaluations``."""

    def move(self):
        importlib.import_module("evaluations.migrations.0002_move_permissions").move(apps, None)

    def test_old_permissions_are_renamed_in_place(self):
        old = ContentType.objects.get_or_create(app_label="members", model="clubpermission")[0]
        execute = Permission.objects.create(content_type=old, codename="execute_evaluations", name="Execute evaluations")
        create = Permission.objects.create(content_type=old, codename="create_evaluations", name="Create evaluations")
        group = Group.objects.create(name="Coaches")
        group.permissions.add(execute, create)

        self.move()

        self.assertEqual(set(group.permissions.values_list("content_type__app_label", "codename")), {("evaluations", "execute_evaluations"), ("evaluations", "manage_evaluations")})

    def test_nothing_to_move(self):
        self.move()  # a club that never had them: no error, nothing invented
        self.assertFalse(Permission.objects.filter(codename="manage_evaluations", content_type__app_label="members").exists())
