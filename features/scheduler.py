"""In-process replacement for host cron and the old live_score_poller container -- see
DEPLOYMENT.md's "Scheduled jobs" for the operational picture and the history this replaces
(Celery worker/beat -> host cron -> this).

features.jobs.JOB_REGISTRY is the schedule, in code, in git -- there is nothing left to
configure on the host. Each tick runs the job's command through call_command exactly the
way cron's `docker compose run --rm web python manage.py <job>` did, so it goes through the
same features.commands.ScheduledJobCommand execute() path -- same JobRun row, same
JobToggle/Maintenance checks, same args (the two billing jobs' --commit included). Only
*what triggers a run* changed, not what a run is.

Runs as one APScheduler BackgroundScheduler thread, started from gunicorn.conf.py's
when_ready hook -- which fires once, in the master process, after `--preload` has already
forked the workers (see that file). That placement is what keeps this to exactly one
scheduler thread per node regardless of --workers, without a single request-serving worker
ever owning it.

**Leader election** (`_try_acquire_or_renew` below) is what keeps it to exactly one
scheduler *fleet-wide* if this ever runs on more than one node -- the same "one scheduler"
rule DEPLOYMENT.md already states for the multi-server/AWS section, enforced here instead
of by deployment convention. It rides the cache (Redis in production, so the lease is
visible across nodes; falls back to LocMemCache in dev/tests, where there is only ever one
process anyway and acquisition always succeeds). This is a cooperative lease with a small,
accepted race: two processes can both believe they hold it for at most one renewal window
around the moment a lease expires (a crashed leader, a slow GC pause) -- worst case is one
job tick running twice, which every job already tolerates (JobToggle/Maintenance checks
aside, the domain commands themselves are idempotent per run -- "Nothing to renew", "Nothing
overdue past grace", etc.). A hard distributed lock (Redis SET NX + Lua-verified release) is
more machinery than a 1-5 club platform's job volume justifies; revisit if that ever stops
being true.
"""

import logging
import os
import socket
import threading
import uuid

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.db import connections

from features.jobs import JOB_REGISTRY

logger = logging.getLogger(__name__)

LEADER_CACHE_KEY = "scheduler:leader"

#: How long a held lease is valid without renewal. A dead leader's schedule goes silent for
#: at most this long before another process picks it up.
LEASE_SECONDS = 30

#: How often the leader renews its own lease -- well under LEASE_SECONDS so one slow tick
#: or GC pause doesn't cost it leadership before the next renewal even gets a chance to run.
RENEW_INTERVAL_SECONDS = 10

#: Misfires (the process was down, or a tick ran long) within this window still run once
#: coalesced; older ones are dropped rather than run back-to-back -- matches cron's own
#: behaviour of not "catching up" missed runs, just picking up from here.
MISFIRE_GRACE_SECONDS = 300

_TOKEN = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"

_scheduler = None
_leader_thread = None
_stopping = threading.Event()


def _run_job(name, command, args):
    """One JOB_REGISTRY tick. Runs on one of APScheduler's own executor threads, which --
    like the old run_live_score_poller loop and controlpanel.views.JobRunNowView's own
    background thread -- never goes through the request/response cycle that normally closes
    stale connections, hence the explicit close in `finally`."""
    try:
        call_command(command, *args)
    except Exception:
        # features.commands.ScheduledJobCommand already wrote a JobRun(FAILURE) row and
        # logged this itself (Maintenance/JobToggle refusals included) -- catching it here
        # only stops one bad tick from reaching APScheduler's own executor and taking the
        # scheduler thread down with it.
        logger.exception("scheduler.job_failed name=%s", name)
    finally:
        connections.close_all()


def _build_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(timezone=settings.TIME_ZONE)
    for name, meta in JOB_REGISTRY.items():
        trigger = dict(meta["trigger"])
        scheduler.add_job(
            _run_job,
            trigger=trigger.pop("trigger"),
            kwargs={"name": name, "command": meta["command"], "args": meta["args"]},
            id=name,
            max_instances=1,  # mirrors the old crontab's `flock -n`: a still-running tick makes the next one skip, not queue.
            coalesce=True,  # one catch-up run for however many ticks were missed, not one per tick.
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
            **trigger,
        )
    return scheduler


def _try_acquire_or_renew() -> bool:
    """True if this process holds the leader lease after this call -- either it just
    acquired an unheld one (cache.add is atomic: exactly one caller across the fleet wins a
    given expiry window) or it already held it and renewed."""
    if cache.add(LEADER_CACHE_KEY, _TOKEN, LEASE_SECONDS):
        return True
    if cache.get(LEADER_CACHE_KEY) == _TOKEN:
        cache.set(LEADER_CACHE_KEY, _TOKEN, LEASE_SECONDS)
        return True
    return False


def _leader_loop():
    """Runs on its own daemon thread for the lifetime of the process: acquires or renews
    the lease every RENEW_INTERVAL_SECONDS, starting the scheduler the moment leadership is
    won and stopping it the moment it's lost (a crash elsewhere let another process win it
    back, or Redis itself became briefly unreachable) -- checked far more often than
    LEASE_SECONDS so a genuinely healthy leader essentially never flaps."""
    global _scheduler

    while not _stopping.is_set():
        is_leader = _try_acquire_or_renew()

        if is_leader and _scheduler is None:
            logger.info("scheduler.leader_acquired token=%s", _TOKEN)
            _scheduler = _build_scheduler()
            _scheduler.start()
        elif not is_leader and _scheduler is not None:
            logger.info("scheduler.leader_lost token=%s", _TOKEN)
            _scheduler.shutdown(wait=False)
            _scheduler = None

        _stopping.wait(RENEW_INTERVAL_SECONDS)


def _warm_cache_backend_import():
    """Forces every module django_redis lazily imports via import_string to finish
    importing now, synchronously, on whichever thread calls start() -- which
    gunicorn.conf.py's when_ready hook does in the master's own main thread, before the
    arbiter forks the initial workers (when_ready fires before that fork, not after,
    despite the name).

    Without this, _leader_loop's first cache.add() below is the *first* cache access
    anywhere in this process's life (no app's ready() touches Redis eagerly, and no request
    has been served yet) -- and it happens on a background thread, racing gunicorn's own
    imminent fork of the initial workers. If that fork lands mid-import, the forked worker
    inherits a half-initialized module already sitting in sys.modules (fork() copies it
    as-is, in-progress or not) -- every later cache access in that worker then fails with
    "module ... has no attribute 'X'" instead of finishing the import, because Python sees
    the module already in sys.modules and never re-runs it. This bit exactly one of two
    workers on first deploy (django_redis.client.DefaultClient), and again, one layer
    deeper, on the very next deploy (django_redis.serializers.pickle.PickleSerializer, only
    imported when DefaultClient is actually *constructed*, not merely referenced) --
    /healthz (round-robined to the other worker) looked fine both times while everything
    else 500'd.

    That history is why this builds the real client instead of hand-picking import_string
    calls to replay one at a time: django_redis's CLIENT_CLASS, SERIALIZER, COMPRESSOR, and
    connection-factory classes (pool_cls/redis_client_cls) are each resolved lazily, from
    three different modules, only once something actually constructs a DefaultClient --
    missing any one of them leaves the same race for whichever request first exercises that
    particular path. Accessing caches["default"].client constructs the real DefaultClient
    (and its ConnectionFactory), which is what actually resolves all of them in one place --
    the same object graph a real request would build, just built here first. What it
    deliberately does NOT do is issue a command: redis-py's connection pool is itself lazy,
    so nothing here opens an actual socket, which fork()-sharing into every worker would
    otherwise corrupt (multiple processes issuing commands over one inherited TCP connection
    corrupts Redis's own protocol framing) -- confirmed by reading the installed
    django_redis/client/default.py and pool.py: DefaultClient.__init__ and
    ConnectionFactory.__init__ only import and instantiate classes, no I/O.

    A no-op on LocMemCache (dev/test, no DJANGO_REDIS_URL) -- it has no `.client` at all,
    and none of this lazy-import machinery to race in the first place.
    """
    from django.core.cache import caches

    getattr(caches["default"], "client", None)


def start():
    """Called once from gunicorn.conf.py's when_ready hook, in the master process only."""
    global _leader_thread

    if _leader_thread is not None:
        return

    logger.info("scheduler.start pid=%s token=%s", os.getpid(), _TOKEN)
    _warm_cache_backend_import()
    _stopping.clear()
    _leader_thread = threading.Thread(target=_leader_loop, name="rosterchief-scheduler", daemon=True)
    _leader_thread.start()


def stop():
    """Called from gunicorn.conf.py's on_exit hook. Best-effort: releases the lease
    immediately (if we hold it) so a healthy replacement node doesn't wait out the full
    LEASE_SECONDS during a routine redeploy, then stops the local scheduler."""
    global _scheduler, _leader_thread

    _stopping.set()
    if _leader_thread is not None:
        _leader_thread.join(timeout=RENEW_INTERVAL_SECONDS)
        _leader_thread = None

    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None

    if cache.get(LEADER_CACHE_KEY) == _TOKEN:
        cache.delete(LEADER_CACHE_KEY)

    logger.info("scheduler.stopped pid=%s", os.getpid())
