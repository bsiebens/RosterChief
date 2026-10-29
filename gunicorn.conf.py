"""Gunicorn config for the app server -- sits alongside the CLI flags in the systemd unit
(deploy/systemd/rosterchief-web.service: workers, threads, timeouts, etc). This file exists for
what CLI flags can't do: hooks.

Scheduled jobs do NOT run in here: Celery beat + worker do (see rosterchief/celery.py,
deploy/systemd/rosterchief-beat.service and rosterchief-worker.service).
"""


def post_fork(server, worker):
    """Give every worker its own cache (Redis) connections, never the master's.

    History: when the scheduler thread lived in the master it used Redis every tick, so a worker
    forked mid-call inherited the pool with a lock stuck held by a thread that does not exist in
    the child, and every Redis call in that worker failed with
    redis.exceptions.ChildDeadlockedError. The scheduler is gone (Celery runs the jobs now), which removes the cause;
    this stays as a cheap safety net so a worker can
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
