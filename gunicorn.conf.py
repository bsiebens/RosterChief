"""Gunicorn config for the app server -- sits alongside the CLI flags in Dockerfile's CMD
(workers, threads, timeouts, etc). This file exists for exactly one thing CLI flags can't
do: when_ready/on_exit hooks, which start and stop the in-process job scheduler
(features/scheduler.py) -- see that module's own docstring for the full picture. when_ready
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


def on_exit(server):
    from features.scheduler import stop

    stop()
