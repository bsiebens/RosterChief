"""So the command bar's status indicator (base.html) can reflect real job health on every
control panel page, not just the dashboard, without every view remembering to pass it.

Guarded to controlpanel pages only -- unlike features.context_processors.maintenance (a
cached read, cheap anywhere), this runs a real query, and every other page on the platform
(club subdomains, the public site) has no command bar to show it on.
"""

from .services.jobs import FAILURE_WINDOW_HOURS, recent_job_failures


def job_health(request):
    if not (request.resolver_match and request.resolver_match.app_name == "controlpanel"):
        return {}

    # A count, not the queryset: every consumer only ever shows the number.
    from notifications import sandbox

    context = {"sandbox_on": sandbox.enabled(), "failed_jobs": recent_job_failures().count(), "failure_window_hours": FAILURE_WINDOW_HOURS}
    # Only for the superusers who see the Push tab: one cheap count of unresolved alerts.
    user = getattr(request, "user", None)
    if context["sandbox_on"]:
        context["sandbox_expires_at"] = sandbox.status()["expires_at"]
    if user is not None and user.is_superuser:
        from notifications.models import PushAlert

        context["open_push_alerts"] = PushAlert.objects.filter(resolved_at__isnull=True).count()
    return context
