"""Moderation (``chat.moderate``): chats with minors, reports, reading along. Every look and every action is audit-logged."""

import calendar
import datetime

from django.db.models import Q
from django.utils import timezone
from ninja.errors import HttpError

from chat.models import Conversation, MessageReport, Participant
from chat.services import access, people
from members.models import FamilyMembership, Member
from members.services import audit as audit_trail


def audit(user, action, conversation=None, *, extra=None):
    audit_trail.record(user, action, target=conversation, target_label=(conversation.title or conversation.kind) if conversation else "", changes={k: {"from": None, "to": v} for k, v in (extra or {}).items()})


def minor_member_ids():
    """Ids of every minor in the club (date of birth under 18, or a family child without a date of birth)."""
    today = timezone.localdate()
    year = today.year - people.ADULT_AGE
    cutoff = datetime.date(year, today.month, min(today.day, calendar.monthrange(year, today.month)[1]))
    undated_children = FamilyMembership.objects.filter(role=FamilyMembership.FamilyRole.CHILD, member__date_of_birth__isnull=True).values("member_id")
    return Member.objects.filter(Q(date_of_birth__gt=cutoff) | Q(pk__in=undated_children)).values("pk")


def conversations(user, *, minors_only=True):
    audit(user, "chat.moderation_listed", extra={"minors_only": minors_only})
    qs = Conversation.objects.all()
    if minors_only:
        qs = qs.filter(pk__in=access.active(Participant.objects.filter(member__in=minor_member_ids())).values("conversation_id"))
    return qs.order_by("-last_message_at", "-created")


def get_conversation(user, conversation_id) -> Conversation:
    c = Conversation.objects.select_related("team").filter(pk=conversation_id).first()
    if c is None:
        raise HttpError(404, "Not found.")
    return c


def reports(user):
    audit(user, "chat.reports_listed")
    return MessageReport.objects.select_related("message", "message__conversation", "message__conversation__team", "message__author", "reporter").order_by("handled_at", "-created")


def handle_report(user, me, report_id) -> MessageReport:
    r = MessageReport.objects.select_related("message__conversation").filter(pk=report_id).first()
    if r is None:
        raise HttpError(404, "Not found.")
    if r.handled_at is None:
        r.handled_at, r.handled_by = timezone.now(), me
        r.save(update_fields=["handled_at", "handled_by", "modified"])
        audit(user, "chat.report_handled", r.message.conversation, extra={"report": str(r.pk), "message": str(r.message_id)})
    return r
