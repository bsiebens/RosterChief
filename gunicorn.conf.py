"""Gunicorn config for the app server -- sits alongside the CLI flags in Dockerfile's CMD
(workers, threads, timeouts, etc). This file exists for what CLI flags can't do: hooks.

The in-process job scheduler (features/scheduler.py) is started in each WORKER (post_worker_init)
and stopped when the worker exits (worker_exit); a Redis lease makes exactly one of them run the
schedule. It must NOT be started from `when_ready` or any other master-side hook: those run in the
process that forks the workers, and a thread there makes every fork a gamble -- whatever lock the
thread holds at that instant (the DNS resolver's, a connection pool's) is copied locked into the
new worker, which then hangs forever on its first use of it. See features/scheduler.py. A test
(features.tests.GunicornConfigTests) fails if a master-side hook comes back.
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rosterchief.settings")


def post_worker_init(worker):
    """Runs in the worker, after it has loaded the app and before it serves anything. A worker
    never forks again, so a thread started here cannot leave a lock held in a child."""
    # Idempotent -- with --preload (see Dockerfile's CMD) the app was already imported, and
    # django.setup() already called, in the master; this keeps the file independent of that.
    django.setup()

    from features.scheduler import start

    start()
    worker.log.info("rosterchief.scheduler started in worker pid=%s", os.getpid())


def worker_exit(server, worker):
    """Runs in the worker as it exits (a recycle after --max-requests, a deploy): release the
    scheduler lease at once so another worker takes over within seconds, not LEASE_SECONDS."""
    from features.scheduler import stop

    stop()


def post_fork(server, worker):
    """Give every worker its own cache (Redis) connections, never the master's.

    History: when the scheduler thread lived in the master it used Redis every tick, so a worker
    forked mid-call inherited the pool with a lock stuck held by a thread that does not exist in
    the child, and every Redis call in that worker failed with
    redis.exceptions.ChildDeadlockedError. The scheduler no longer lives in the master (see the
    module docstring), which removes the cause; this stays as a cheap safety net so a worker can
    never share the master's connections whatever else ends up there.

    Two things carry the inherited pool into the worker, and both are dropped here: django-redis
    keeps its pools in a process-global registry (ConnectionFactory._pools), and Django's cache
    handler keeps the master's client objects in per-thread storage. The first cache access in
    the worker then builds fresh clients and pools under the worker's own pid."""
    from asgiref.local import Local
    from django.core.cache import caches

    try:
        from django_redis.pool import ConnectionFactory
    except ImportError:  # no Redis backend installed (e.g. a LocMem-only setup)
        pass
    else:
        ConnectionFactory._pools.clear()
    caches._connections = Local(caches.thread_critical)
