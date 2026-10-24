"""Backfill / repair the GameStat rows (events.services.game_stats) from the timelines already
stored -- needed once after the team analytics shipped, since only timelines fetched or changed
afterwards get their stats written; also after a roster fix changes who a name or number matches.

    uv run python manage.py rebuild_game_stats                       # every stored timeline whose stats are missing or stale
    uv run python manage.py rebuild_game_stats --club my-club --since 2026-09-01
    uv run python manage.py rebuild_game_stats --force --dry-run

Works from the stored timeline data -- nothing is fetched, so it doesn't need the
"game_timeline" flag. A game whose stats already agree with its timeline is left alone
unless --force is passed. The nightly settle_game_timelines keeps recent games in agreement
too; this is for everything older.
"""

import datetime

from events.models import GameTimeline
from events.services.game_stats import ensure_game_stats, stats_out_of_sync, sync_game_stats
from features.commands import CommandError, MaintenanceAwareCommand


class Command(MaintenanceAwareCommand):
    help = "Rebuild per-game stats (goals, assists, penalty minutes) from the stored game timelines."

    def add_arguments(self, parser):
        parser.add_argument("--club", help="Only this club (its slug). Default: every club.")
        parser.add_argument("--since", type=datetime.date.fromisoformat, help="Games from this date (YYYY-MM-DD). Default: all stored timelines.")
        parser.add_argument("--force", action="store_true", help="Rewrite every game's stats, even when they already agree with the timeline.")
        parser.add_argument("--dry-run", action="store_true", help="Report what would be rewritten without writing anything.")

    def handle(self, *args, **options):
        timelines = GameTimeline.objects.exclude(data={}).select_related("event", "event__club", "event__location", "event__season").order_by("event__start")
        if options["club"]:
            timelines = timelines.filter(event__club__slug=options["club"])
            if not timelines.exists():
                raise CommandError(f"No stored timelines for a club with slug {options['club']!r}.")
        if options["since"]:
            timelines = timelines.filter(event__start__date__gte=options["since"])

        rewritten = unchanged = 0
        for timeline in timelines.iterator():
            event, document = timeline.event, timeline.data
            if options["dry_run"]:
                stale = options["force"] or stats_out_of_sync(event, document)
                rewritten += stale
                unchanged += not stale
            elif options["force"]:
                sync_game_stats(event, document)
                rewritten += 1
            elif ensure_game_stats(event, document):
                rewritten += 1
            else:
                unchanged += 1

        verb = "Would rewrite" if options["dry_run"] else "Rewrote"
        self.stdout.write(self.style.SUCCESS(f"{verb} stats for {rewritten} games ({unchanged} already up to date)."))
