"""Opt-in, production-safe request tracing -- query count/time, slow-query text, and
total template-render time, logged for any request slower than a threshold. Built for
diagnosing a genuinely slow page (the control panel dashboard) without installing
django-debug-toolbar in production, which nobody wants running there permanently.

Off by default (``DJANGO_PERFORMANCE_LOGGING=false``). Flip it on for a deployment,
read the logs, flip it back off -- it isn't meant to run permanently, and the query
timing wrapper and the Template.render patch below both have a real (if small) per
-query/per-render cost, so leaving it on indefinitely would just add the overhead
this exists to measure.

Doesn't log query parameters, only the SQL shape (placeholders, not values) --
django.db.backends.utils.CursorWrapper.execute already keeps params separate from
sql, so there's nothing to scrub, just nothing to add.
"""

import logging
import threading
import time

from django.conf import settings
from django.template.base import Template

logger = logging.getLogger("rosterchief.performance")

#: How much of a single query's SQL text to log -- long enough to identify the query
#: (table, columns, JOINs), short enough that one slow request's log block stays readable.
_SQL_LOG_LENGTH = 300

_state = threading.local()


def _tracking_active() -> bool:
    return getattr(_state, "queries", None) is not None


def start_tracking() -> None:
    _state.queries = []
    _state.template_ms = 0.0
    _state.template_depth = 0


def stop_tracking() -> tuple[list[tuple[float, str]], float]:
    queries = getattr(_state, "queries", [])
    template_ms = getattr(_state, "template_ms", 0.0)
    _state.queries = None
    _state.template_ms = 0.0
    _state.template_depth = 0
    return queries, template_ms


def query_execute_wrapper(execute, sql, params, many, context):
    """Passed to ``connection.execute_wrapper()`` for the lifetime of one request --
    see PerformanceLoggingMiddleware. A no-op (just calls straight through) whenever
    tracking isn't active, so this stays safe as a permanently-installed wrapper."""
    if not _tracking_active():
        return execute(sql, params, many, context)

    start = time.perf_counter()
    try:
        return execute(sql, params, many, context)
    finally:
        _state.queries.append((time.perf_counter() - start, sql))


_original_template_render = Template.render


def _timed_template_render(self, context=None):
    """Replaces Template.render for the process's lifetime (installed once below,
    only when the feature is enabled) -- a no-op passthrough when tracking isn't
    active. Only the outermost call (template_depth == 0) adds to the running total:
    an {% include %}/{% extends %}'d template's own render() call nests inside its
    parent's, and that parent's own elapsed time already covers it -- counting both
    would double-count every nested template's time."""
    if not _tracking_active():
        return _original_template_render(self, context)

    _state.template_depth += 1
    start = time.perf_counter()
    try:
        return _original_template_render(self, context)
    finally:
        elapsed = time.perf_counter() - start
        _state.template_depth -= 1
        if _state.template_depth == 0:
            _state.template_ms += elapsed * 1000


def install_template_timing() -> None:
    Template.render = _timed_template_render


if getattr(settings, "PERFORMANCE_LOGGING_ENABLED", False):
    install_template_timing()


def shorten_sql(sql: str) -> str:
    if len(sql) <= _SQL_LOG_LENGTH:
        return sql
    return sql[:_SQL_LOG_LENGTH] + "…"
