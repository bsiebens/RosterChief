"""Generate seasons ahead of time for every active club.

Meant to run on a schedule (cron): safe to call repeatedly, since generate_seasons
skips whatever already exists. No --dry-run/--commit gate on generation itself --
unlike archiving a club or billing it, creating a future season row is additive
and idempotent, same reasoning as extend_event_series (materialising occurrences).

--resync is different: it can delete rows (any season that doesn't match a
club's *current* season_start/season_duration_months, and isn't referenced by a
membership), so it defaults to reporting only -- pass --commit alongside it to
actually remove anything.
"""

from dateutil.relativedelta import relativedelta
from django.utils import timezone

from club.models import Club
from club.services.seasons import generate_seasons, resync_seasons
from features.commands import MaintenanceAwareCommand


class Command(MaintenanceAwareCommand):
    help = "Generate seasons up to N years ahead for every active club (default 2)."

    def add_arguments(self, parser):
        parser.add_argument("--years", type=int, default=2, help="How many years ahead to generate seasons for (default 2).")
        parser.add_argument("--resync", action="store_true", help="Before generating, remove any existing seasons that don't match the club's current settings (skips any still referenced by a membership).")
        parser.add_argument("--commit", action="store_true", help="With --resync, actually delete the seasons found to be wrong. Without it, --resync only reports what it would remove.")

    def handle(self, *args, **options):
        until = timezone.localdate() + relativedelta(years=options["years"])
        clubs = Club.objects.active()

        if options["resync"]:
            total_removed, total_kept = 0, 0
            for club in clubs:
                removed, kept = resync_seasons(club, until, commit=options["commit"])
                total_removed += len(removed)
                total_kept += len(kept)
                if removed:
                    verb = "Removed" if options["commit"] else "Would remove"
                    self.stdout.write(f"{club}: {verb} {len(removed)} season(s) that no longer match its settings.")
                if kept:
                    self.stdout.write(self.style.WARNING(f"{club}: {len(kept)} season(s) don't match its settings but are still in use, left alone."))

            if not options["commit"] and total_removed:
                self.stdout.write(self.style.WARNING("Dry run -- pass --commit to actually delete these."))
            self.stdout.write(self.style.SUCCESS(f"Resync: {total_removed} season(s) {'removed' if options['commit'] else 'to remove'}, {total_kept} kept (in use)."))

        total = 0
        for club in clubs:
            created = generate_seasons(club, until)
            total += len(created)
            if created:
                self.stdout.write(f"{club}: generated {len(created)} season(s).")

        self.stdout.write(self.style.SUCCESS(f"Done. Generated {total} season(s) across {clubs.count()} club(s)."))
