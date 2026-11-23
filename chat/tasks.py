"""Celery tasks of the chat app. Message pushes run in the club schema they were queued from; the periodic ones loop over the clubs."""

import logging

from celery import shared_task

log = logging.getLogger(__name__)


@shared_task(name="chat.push_message")
def push_message(message_id: str) -> str:
    from .services import push

    return f"{push.push_message_id(message_id)} push(es)"


def _for_each_club(fn, feature="chat"):
    from django_tenants.utils import get_public_schema_name, get_tenant_model, schema_context

    from features.entitlements import resolve_entitlements

    results, failed = [], []
    for club in get_tenant_model().objects.filter(archived_at__isnull=True).exclude(schema_name=get_public_schema_name()):
        try:
            if not resolve_entitlements(club).has(feature):
                continue
            with schema_context(club.schema_name):
                results.append(fn())
        except Exception:
            log.exception("chat task failed for club %s", club.slug)
            failed.append(club.slug)
    return results, failed


@shared_task(name="chat.sync_team_chats")
def sync_team_chats() -> str:
    """Nightly: add new roster members to team chats and let the ones who left drop out (people added by hand stay)."""
    from .services import roster

    results, failed = _for_each_club(roster.sync_all)
    return f"{sum(r['added'] for r in results)} added, {sum(r['left'] for r in results)} left" + (f"; failed: {', '.join(failed)}" if failed else "")


@shared_task(name="chat.apply_retention")
def apply_retention() -> str:
    """Nightly: delete messages (and files) older than the club's retention."""
    from .services import retention

    results, failed = _for_each_club(retention.apply)
    return f"{sum(r['messages'] for r in results)} message(s) removed" + (f"; failed: {', '.join(failed)}" if failed else "")
