from django.core.management.base import BaseCommand

from controlpanel.models import Maintenance


class Command(BaseCommand):
    help = "Turn platform maintenance mode on or off (used by deploy/rc around a production deploy)."

    def add_arguments(self, parser):
        parser.add_argument("state", choices=("on", "off", "status"))
        parser.add_argument("--message", default="", help="Shown to visitors while on (keeps the current message if omitted).")

    def handle(self, *args, state, message, **options):
        maintenance = Maintenance.current()
        if state == "status":
            self.stdout.write("maintenance=on" if maintenance.is_active else "maintenance=off")
            return
        if state == "on":
            if message:
                maintenance.message = message
            maintenance.switch(True)
        else:
            maintenance.switch(False)
        self.stdout.write(f"maintenance={state}")
