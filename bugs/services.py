"""Filing, noting and updating bug reports.

Emailing platform admins on a new report mirrors billing.services.invoices.
notify_admins_of_new_invoice's own reasoning: fail_silently=True here too, since a mail
hiccup must never stop someone's bug report from being filed, and both send from
management and mobile, so it belongs here rather than duplicated at each call site.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone

from rosterchief.mail import send_message

from .models import BugNote, BugReport


def _platform_admin_emails() -> list[str]:
    User = get_user_model()
    return list(User.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).exclude(email="").order_by("email").values_list("email", flat=True))


def notify_admins_of_new_bug(bug: BugReport) -> None:
    recipients = _platform_admin_emails()
    if not recipients:
        return

    context = {"bug": bug, "club": bug.club, "reporter": bug.reported_by}
    subject = render_to_string("bugs/email/new_bug_subject.txt", context).strip()
    text_body = render_to_string("bugs/email/new_bug.txt", context)

    message = EmailMultiAlternatives(subject=subject, body=text_body, from_email=settings.DEFAULT_FROM_EMAIL, to=recipients)
    send_message(message, fail_silently=True)


def file_report(*, club, reported_by, title: str, description: str) -> BugReport:
    """Create a bug report and tell platform admins about it -- the one path both the
    management and mobile "Report a bug" forms go through."""
    bug = BugReport.objects.create(club=club, reported_by=reported_by, title=title, description=description)
    notify_admins_of_new_bug(bug)

    return bug


def add_note(bug: BugReport, *, author, body: str) -> BugNote:
    return BugNote.objects.create(bug=bug, author=author, body=body)


def update_bug(bug: BugReport, *, status: str, priority: str, fixed_version: str) -> BugReport:
    """Apply a control panel edit. fixed_at is derived, not editable directly: it is
    stamped the moment status first becomes Fixed, and cleared the moment it moves away
    from Fixed again -- a re-opened bug must not still show a stale fix date/version to
    the reporter (see BugReport.fixed_at's own docstring)."""
    bug.status = status
    bug.priority = priority
    bug.fixed_version = fixed_version
    if status == BugReport.Status.FIXED:
        if bug.fixed_at is None:
            bug.fixed_at = timezone.now()
    else:
        bug.fixed_at = None
    bug.save(update_fields=["status", "priority", "fixed_version", "fixed_at", "modified"])

    return bug
