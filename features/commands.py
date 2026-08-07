"""Scheduled work refuses to run while the platform is locked down.

Deliberately opt-in, per command, rather than a blanket guard on BaseCommand: maintenance is
usually declared IN ORDER to run `migrate` or `collectstatic`, and a guard that blocked those
would make the mode useless — you would have to turn it off to do the work you turned it on
for. Only the domain jobs (which write club data, archive clubs, or import members) stand
down.
"""

from django.core.management.base import BaseCommand, CommandError

from features.models import Maintenance


class MaintenanceAwareCommand(BaseCommand):
    """A command that must not run while the platform is closed."""

    def execute(self, *args, **options):
        if Maintenance.is_on() and not options.get("ignore_maintenance"):
            raise CommandError("The platform is in maintenance mode; this command stands down. Pass --ignore-maintenance to override.")

        return super().execute(*args, **options)

    def create_parser(self, prog_name, subcommand, **kwargs):
        parser = super().create_parser(prog_name, subcommand, **kwargs)
        parser.add_argument(
            "--ignore-maintenance",
            action="store_true",
            help="Run even though the platform is in maintenance mode.",
        )
        return parser
