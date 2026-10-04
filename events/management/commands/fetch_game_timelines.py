"""Fill in timelines for games that were played before timelines were switched
on -- the manual backfill (GAME_TIMELINE.md §9.6). Nothing runs this on a
schedule; new games get their timeline from the live poll, and recently
finished ones are kept up to date by settle_game_timelines.

    uv run python manage.py fetch_game_timelines                 # current season, every club with the flag
    uv run python manage.py fetch_game_timelines --club my-club --since 2026-09-01 --dry-run

Only clubs with the "game_timeline" flag (and the game's competition flag) are
fetched. A game that already has a finalized timeline is skipped unless
--refetch is passed. A game older than the settle window is marked finalized
straight away, since nothing will re-fetch it later.
"""

import datetime

from django.utils import timezone

from club.models import Club, Season
from events.models import GameTimeline
from events.services.competitions import CompetitionFetchError, competition_for
from events.services.game_timeline import FAILED, SETTLE_WINDOW, UNAVAILABLE, fetch_and_store, games_for_backfill, timeline_enabled
from features.commands import CommandError, MaintenanceAwareCommand


class Command(MaintenanceAwareCommand):
    help = "Fetch and store timelines for games already played (default: since the start of each club's current season)."

    def add_arguments(self, parser):
        parser.add_argument("--club", help="Only this club (its slug). Default: every club with the game_timeline flag.")
        parser.add_argument("--since", type=datetime.date.fromisoformat, help="Games from this date (YYYY-MM-DD). Default: the start of each club's current season.")
        parser.add_argument("--refetch", action="store_true", help="Also re-fetch games whose timeline is already finalized.")
        parser.add_argument("--dry-run", action="store_true", help="List the games that would be fetched without fetching anything.")

    def handle(self, *args, **options):
        clubs = Club.objects.active().order_by("name")
        if options["club"]:
            clubs = clubs.filter(slug=options["club"])
            if not clubs.exists():
                raise CommandError(f"No active club with slug {options['club']!r}.")

        counts = {"fetched": 0, "skipped": 0, "failed": 0}
        for club in clubs:
            if not timeline_enabled(club):
                if options["club"]:
                    raise CommandError(f"The game_timeline flag isn't on for {club.slug}.")
                continue
            since = options["since"]
            if since is None:
                season = Season.covering(club, timezone.localdate())
                if season is None:
                    continue
                since = season.start_date
            since_dt = timezone.make_aware(datetime.datetime.combine(since, datetime.time.min))

            finalized = set(GameTimeline.objects.filter(event__club=club, finalized_at__isnull=False).values_list("event_id", flat=True))
            for event in games_for_backfill(since=since_dt, club=club):
                if event.pk in finalized and not options["refetch"]:
                    counts["skipped"] += 1
                    continue
                label = f"{club.slug}  {timezone.localtime(event.start):%Y-%m-%d %H:%M}  {event.competition}  {event.title}"
                if options["dry_run"]:
                    self.stdout.write(label)
                    counts["fetched"] += 1
                    continue
                try:
                    source = competition_for(event)
                except CompetitionFetchError:
                    source = None
                outcome = fetch_and_store(event, source) if source is not None else UNAVAILABLE
                if outcome == UNAVAILABLE:
                    counts["skipped"] += 1
                    continue
                if outcome == FAILED:
                    counts["failed"] += 1
                    self.stdout.write(self.style.WARNING(f"{label}: failed"))
                    continue
                counts["fetched"] += 1
                if event.start < timezone.now() - SETTLE_WINDOW:
                    GameTimeline.objects.filter(event=event, status=GameTimeline.Status.FINAL).update(finalized_at=timezone.now())
                if options["verbosity"] >= 2:
                    self.stdout.write(label)

        verb = "Would fetch" if options["dry_run"] else "Fetched"
        self.stdout.write(self.style.SUCCESS(f"{verb} {counts['fetched']} game timeline(s); {counts['skipped']} skipped, {counts['failed']} failed."))
