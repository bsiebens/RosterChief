"""Creating and previewing a ``FormSend`` -- the logic both the Django ``/manage/forms/*/sends`` views and the
management API call, so the two cannot drift."""

from django.db import transaction

from formbuilder.models import FormSend

from .audience import effective_members
from .notifications import dispatch_notify_form_send


def create_send(send_form, created_by, dispatch=None):
    """Save a validated ``FormSendForm`` as a new send authored by ``created_by`` (a Member or None) and
    start notifying its audience in the background (``dispatch(send_id)``). Returns the saved ``FormSend``."""
    send_form.instance.created_by = created_by
    send = send_form.save()
    (dispatch or dispatch_notify_form_send)(str(send.pk))
    return send


def audience_preview(send_form) -> list:
    """Who a *not yet saved* send form would address: the members ``effective_members`` resolves for it.

    The form is saved inside a savepoint that is rolled back, so the audience is resolved by exactly the same
    code as for a real send and nothing is persisted. Returns a list of ``Member``."""
    with transaction.atomic():
        send = send_form.save()
        members = list(effective_members(send).order_by("last_name", "first_name"))
        transaction.set_rollback(True)
    return members


def send_audience(send: FormSend) -> list:
    """The members an existing send is addressed to, by name."""
    return list(effective_members(send).order_by("last_name", "first_name"))
