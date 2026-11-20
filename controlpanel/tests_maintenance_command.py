"""The ``maintenance`` management command that deploy/rc runs around a production deploy."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from controlpanel.models import Maintenance


def run(*args) -> str:
    out = StringIO()
    call_command("maintenance", *args, stdout=out)
    return out.getvalue().strip()


class MaintenanceCommandTests(TestCase):
    def test_on_off_and_status(self):
        self.assertEqual(run("status"), "maintenance=off")
        self.assertEqual(run("on", "--message", "Back at noon"), "maintenance=on")
        self.assertTrue(Maintenance.current().is_active)
        self.assertEqual(Maintenance.current().message, "Back at noon")
        self.assertEqual(run("status"), "maintenance=on")
        self.assertEqual(run("off"), "maintenance=off")
        self.assertFalse(Maintenance.current().is_active)

    def test_on_without_message_keeps_the_existing_one(self):
        run("on", "--message", "Upgrading")
        run("off")
        run("on")
        self.assertEqual(Maintenance.current().message, "Upgrading")
