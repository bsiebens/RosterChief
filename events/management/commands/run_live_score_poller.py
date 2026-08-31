import logging
import os
import signal
import threading
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connections

logger = logging.getLogger(__name__)

#: Matches the crontab cadence poll_live_game_results used to run at (see DEPLOYMENT.md's
#: "Scheduled jobs" / "Long-running processes"). Kept a plain constant rather than a
#: --interval flag: nothing else reads this value, and a different interval would just be a
#: different job.
INTERVAL_SECONDS = 60


class Command(BaseCommand):
    help = (
        "Long-running replacement for cron's once-a-minute poll_live_game_results invocation "
        "(see DEPLOYMENT.md's 'Long-running processes'). Runs the same command in-process on a "
        "60-second interval instead of paying a fresh container start/Django import every tick. "
        "Deliberately NOT a ScheduledJobCommand itself -- poll_live_game_results still is, so "
        "every tick gets its own JobRun row and JobToggle/Maintenance check exactly like a cron "
        "invocation would; this is just the loop that calls it. Intended to run as its own "
        "compose service with restart: unless-stopped -- that's what makes it resilient to a "
        "crash: a tick failure is caught and logged so the loop itself keeps going, and if the "
        "process dies anyway (OOM, an unhandled signal), Docker restarts the container rather "
        "than leaving live scores unpolled until someone notices."
    )

    def handle(self, *args, **options):
        logger.info("live_score_poller.start pid=%s interval=%s", os.getpid(), INTERVAL_SECONDS)

        # SIGTERM is what `docker compose stop`/`down` sends before its grace period expires
        # and escalates to SIGKILL. Setting this Event instead of letting the default handler
        # kill the process mid-tick means an in-flight poll finishes and its JobRun row gets
        # closed out properly, rather than being left stuck at STARTED forever. It's an Event
        # (not a plain bool) specifically so the wait below can be interrupted immediately --
        # plain time.sleep() is transparently restarted after a signal (PEP 475), so a bool flag
        # checked only once sleep() returns would leave up to INTERVAL_SECONDS between the signal
        # arriving and the process actually exiting, past Docker's default 10s SIGKILL grace
        # period.
        stopping = threading.Event()

        def _stop(signum, frame):
            logger.info("live_score_poller.stopping signum=%s", signum)
            stopping.set()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        while not stopping.is_set():
            tick_started = time.monotonic()
            try:
                call_command("poll_live_game_results")
            except Exception:
                # poll_live_game_results (a ScheduledJobCommand) already logged this to its own
                # JobRun row and re-raised -- catching it here is what keeps one bad tick (a
                # database hiccup, an unexpected exception past fetch_game_info's own per-event
                # handling) from taking the whole poller down with it. Docker's restart policy
                # stays the backstop for whatever this can't recover from on its own.
                logger.exception("live_score_poller.tick_failed")
            finally:
                # Mirrors controlpanel.views.JobRunNowView's own connections.close_all() after
                # each off-cycle unit of work: this loop never goes through the request/response
                # cycle that normally closes stale connections, so without this a connection
                # dropped by Postgres (idle timeout, a restart) between ticks would fail every
                # tick after it instead of just reconnecting on the next one.
                connections.close_all()

            elapsed = time.monotonic() - tick_started
            sleep_for = max(0.0, INTERVAL_SECONDS - elapsed)
            stopping.wait(sleep_for)

        logger.info("live_score_poller.stopped pid=%s", os.getpid())
