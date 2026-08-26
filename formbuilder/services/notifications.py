"""Notifying a FormSend's audience -- fire-and-forget, dispatched from a live
web request (management.views.FormSendCreateView.form_valid), not scheduled.
Same shape as events.services.notifications.notify_new_event/
dispatch_notify_new_event: a plain background thread instead of a Celery
task, since there's no worker/beat left to hand it off to (see
DEPLOYMENT.md's "Scheduled jobs").
"""

import threading

from django.db import connections
from django.utils import timezone
from django.utils.translation import gettext as _

from formbuilder.models import FormSend
from notifications.services import notify_members

from .audience import effective_members


def notify_form_send(send_id):
    """Scheduled right after a staff member creates a FormSend -- notifies the
    send's whole initial audience (nobody's submitted yet at creation time,
    so effective_members == members_not_yet_submitted here)."""
    send = FormSend.objects.filter(pk=send_id).select_related("club", "form").first()
    if send is None:
        return "Skipped: send no longer exists."

    members = effective_members(send)
    if not members:
        return "Skipped: no one to notify."

    if send.closes_at is not None:
        deadline = _(" by %(date)s") % {"date": timezone.localtime(send.closes_at).strftime("%d %b")}
    else:
        deadline = ""
    body = _("Please complete “%(form)s”%(deadline)s.") % {"form": send.form.title, "deadline": deadline}
    notifications = notify_members(members, club=send.club, title=send.form.title, body=body, source=send)
    return f"Notified {len(notifications)} member(s)."


def dispatch_notify_form_send(send_id):
    """Runs notify_form_send on a daemon background thread so the request that
    just created the send doesn't wait on it. connections.close_all() in the
    finally is load-bearing: a manually-spawned thread doesn't get Django's
    usual per-request connection teardown, so skipping it leaks one DB
    connection per dispatch."""

    def _run():
        try:
            notify_form_send(send_id)
        finally:
            connections.close_all()

    threading.Thread(target=_run, daemon=True).start()
