"""Gunicorn config for the app server -- sits alongside the CLI flags in Dockerfile's CMD
(workers, threads, timeouts, etc). This file exists for exactly one thing CLI flags can't
do: when_ready/on_exit hooks, which start and stop the in-process job scheduler
(features/scheduler.py) -- see that module's own docstring for the full picture -- and the
post_fork hook that keeps workers from inheriting the master's Redis connections. when_ready
fires once, in the master process, after the Arbiter has already spawned the workers, which
is what keeps the scheduler to exactly one thread per node regardless of --workers: any
thread started here exists only in the (by then already forked-away) master, never in a
worker.
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rosterchief.settings")


def when_ready(server):
    # Idempotent -- with --preload (see Dockerfile's CMD), the Arbiter already imported the
    # WSGI app in this same master process before this hook ever fires, which already called
    # django.setup() once. Called again explicitly so this file doesn't silently depend on
    # --preload always being the way this is run.
    django.setup()

    from features.scheduler import start

    start()
    server.log.info("rosterchief.scheduler started in master pid=%s", os.getpid())


def post_fork(server, worker):
    """Give every worker its own cache (Redis) connections, never the master's.

    The master runs the scheduler thread, which uses Redis every tick (its leader lease), so
    the master holds a live connection pool. A worker forked while that thread was inside the
    pool inherits it with a lock stuck held by a thread that does not exist in the child (the
    pool's own, which wedges redis-py's fork handling, which then gives up on every other
    thread): every Redis call in that worker fails with redis.exceptions.ChildDeadlockedError
    (the maintenance check runs first on every request, so the whole worker returns 500s)
    until it is recycled.

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


def on_exit(server):
    from features.scheduler import stop

    stop()
