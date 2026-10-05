"""Give every event that has no gathering time one, a fixed number of minutes
before its start. An event that already has a gathering time is never touched.

    uv run python manage.py set_gathering_times 45
    uv run python manage.py set_gathering_times 60 --club my-club --kind game --dry-run

By default only upcoming, non-cancelled events are considered; ``--include-past``
widens that to past events too. ``--dry-run`` lists what would change without
saving anything.

Written with one bulk UPDATE rather than a save() per event: Event's post_save
receiver (events/signals.py) re-syncs attendances and referee/official invites,
none of which depend on the gathering time, so there's nothing to re-run -- and
doing it per event would be slow on a large club.
"""

import datetime

from django.db.models import F
from django.utils import timezone

from club.models import Club
from events.models import Event
from features.commands import CommandError, MaintenanceAwareCommand


class Command(MaintenanceAwareCommand):
    help = "Set the gathering time to N minutes before the start for events without one. Events that already have a gathering time are skipped."

    def add_arguments(self, parser):
        parser.add_argument("minutes", type=int, help="How many minutes before the start the gathering time is set.")
        parser.add_argument("--club", help="Only this club's events (its slug). Default: every club.")
        parser.add_argument("--kind", choices=Event.EventKind.values, action="append", help="Only events of this kind; repeat for several. Default: every kind.")
        parser.add_argument("--include-past", action="store_true", help="Also set it on events that have already started. Default: upcoming events only.")
        parser.add_argument("--dry-run", action="store_true", help="List the events that would get a gathering time without saving anything.")

    def handle(self, *args, **options):
        minutes = options["minutes"]
        if minutes <= 0:
            raise CommandError("minutes must be a positive number.")

        events = Event.objects.filter(cancelled=False)
        if options["club"]:
            club = Club.objects.filter(slug=options["club"]).first()
            if club is None:
                raise CommandError(f"No club with slug {options['club']!r}.")
            events = events.filter(club=club)
        if options["kind"]:
            events = events.filter(kind__in=options["kind"])
        if not options["include_past"]:
            events = events.filter(start__gte=timezone.now())

        skipped = events.filter(gathering__isnull=False).count()
        to_set = events.filter(gathering__isnull=True)
        offset = datetime.timedelta(minutes=minutes)

        if options["dry_run"] or options["verbosity"] >= 2:
            for event in to_set.select_related("club").order_by("start"):
                gathering = timezone.localtime(event.start - offset)
                self.stdout.write(f"{event.club.slug}  {timezone.localtime(event.start):%Y-%m-%d %H:%M}  gathering {gathering:%H:%M}  {event.title}")

        if options["dry_run"]:
            count = to_set.count()
            self.stdout.write(self.style.SUCCESS(f"Would set the gathering time {minutes} min before the start on {count} event(s); {skipped} already have one and would be skipped."))
            return

        updated = to_set.update(gathering=F("start") - offset)
        self.stdout.write(self.style.SUCCESS(f"Set the gathering time {minutes} min before the start on {updated} event(s); skipped {skipped} that already had one."))
