"""Fire-and-forget background work off the request thread.

Every automated notification in this app (email through rosterchief.mail.send_message,
web push through mobile.services.push) is a plain HTTP call to an external service that can
take anywhere from tens of milliseconds to a few seconds per recipient -- fine to pay once,
not fine to make a form submission or admin action wait on a whole fan-out of those before
the response comes back.

There is no task queue to hand this off to (see DEPLOYMENT.md's "Scheduled jobs", and
features/scheduler.py's own docstring for the *periodic* job infrastructure this is
deliberately not reusing: that scheduler is leader-elected across the whole fleet, so a
gunicorn worker -- the process actually handling this request -- never holds a live
APScheduler instance of its own to submit ad-hoc work to; only the elected node's master
process does, and only for the eleven registered jobs). A plain daemon thread per dispatch is
the same trade DEPLOYMENT.md's "Sizing the server" already made for the whole job story: this
app's actual volume doesn't justify a broker or a bounded worker pool, and a daemon thread
costs nothing once the interpreter exits mid-flight -- an acceptable loss for a best-effort
notification. This is the pattern events.services.notifications.dispatch_notify_new_event
established before this helper existed; formbuilder.services.notifications and news.services
each grew their own copy of the same few lines independently, which is what this replaces.
"""

import logging
import threading

from django.db import connections

logger = logging.getLogger(__name__)


def run_in_background(func, *args, **kwargs) -> None:
    """Runs func(*args, **kwargs) on a new daemon thread and returns immediately.

    Exceptions are logged, never re-raised -- by the time one happens, the request that
    triggered this is long gone, so there is nothing left to hand it to. connections.close_all()
    runs afterwards regardless: a manually-spawned thread never goes through the request/
    response cycle that normally closes stale connections for it, so skipping this leaks one
    DB connection per dispatch."""

    def _run():
        try:
            func(*args, **kwargs)
        except Exception:
            logger.exception("background.task_failed func=%s", getattr(func, "__name__", func))
        finally:
            connections.close_all()

    threading.Thread(target=_run, daemon=True).start()
