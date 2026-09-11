import logging
import time

from django.conf import settings
from django.db import connection

from .perf_logging import query_execute_wrapper, shorten_sql, start_tracking, stop_tracking

logger = logging.getLogger("rosterchief.performance")


class PerformanceLoggingMiddleware:
    """Opt-in request-level tracing -- see rosterchief/perf_logging.py's own docstring
    for the full "why" (a production-safe, temporary stand-in for django-debug-toolbar
    when a page is slow and it isn't obvious why). First in MIDDLEWARE so total_ms
    covers every other middleware's own cost too, not just the view.

    Disabled entirely (DJANGO_PERFORMANCE_LOGGING=false, the default): __call__ is a
    straight passthrough, no tracking, no wrapper installed, no per-request cost.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = getattr(settings, "PERFORMANCE_LOGGING_ENABLED", False)
        self.threshold_ms = getattr(settings, "PERFORMANCE_LOGGING_THRESHOLD_MS", 300)
        self.slow_query_count = getattr(settings, "PERFORMANCE_LOGGING_SLOW_QUERIES", 5)

    def __call__(self, request):
        if not self.enabled:
            return self.get_response(request)

        start_tracking()
        start = time.perf_counter()
        try:
            with connection.execute_wrapper(query_execute_wrapper):
                response = self.get_response(request)
        finally:
            total_ms = (time.perf_counter() - start) * 1000
            queries, template_ms = stop_tracking()

        if total_ms >= self.threshold_ms:
            db_ms = sum(elapsed for elapsed, _sql in queries) * 1000
            other_ms = total_ms - db_ms
            status = getattr(response, "status_code", "?")
            logger.warning("slow_request path=%s method=%s status=%s total_ms=%.0f db_queries=%d db_ms=%.0f template_ms=%.0f other_ms=%.0f", request.path, request.method, status, total_ms, len(queries), db_ms, template_ms, other_ms)
            for elapsed, sql in sorted(queries, key=lambda row: row[0], reverse=True)[: self.slow_query_count]:
                logger.warning("  slow_query time_ms=%.1f sql=%s", elapsed * 1000, shorten_sql(sql))

        return response
