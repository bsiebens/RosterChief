from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from members.models import Member
from rosterchief.base import ClubScopedModel, UUIDModel


class BugReport(ClubScopedModel):
    """A bug reported by a club member from the management panel or the mobile app.

    ``club`` (from ClubScopedModel) is the reporter's club at submission time, not
    necessarily "the club the bug is about" -- there is only one platform, so this is
    really just "which club's admin was signed in when they hit Report a bug", kept for
    the control panel's own context (see bugs.services.reports.file_report).

    Everything below ``description`` is platform-admin-only in every surface except the
    control panel -- see bugs.services.reports.visible_fields_for_reporter for exactly
    what a reporter is shown back (title, description, submitted date, status, notes,
    and fixed_at/fixed_version once fixed). priority and reported_by/club are never
    shown to the reporter.
    """

    class Priority(models.TextChoices):
        LOW = "low", _("Low")
        MEDIUM = "medium", _("Medium")
        HIGH = "high", _("High")
        CRITICAL = "critical", _("Critical")

    class Status(models.TextChoices):
        SUBMITTED = "submitted", _("Submitted")
        IN_PROGRESS = "in_progress", _("In progress")
        FIXED = "fixed", _("Fixed")
        WONT_FIX = "wont_fix", _("Won't fix")

    reported_by = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="bug_reports", verbose_name=_("reported by"))
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"))
    priority = models.CharField(_("priority"), max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.SUBMITTED)

    fixed_at = models.DateTimeField(_("fixed at"), null=True, blank=True)
    fixed_version = models.CharField(_("fixed in version"), max_length=50, blank=True, help_text=_("Version/release this was fixed in -- shown back to the reporter once status is Fixed."))

    class Meta:
        verbose_name = _("bug report")
        verbose_name_plural = _("bug reports")
        ordering = ["-created"]

    def __str__(self):
        return self.title

    # Deliberately no clean()/validate_club_scope on reported_by: unlike a
    # Notification's member, a bug's reporter is often pure staff -- a coach with
    # only a StaffAssignment and no ClubMembership row in that club, per
    # ARCHITECTURE.md's RBAC design -- so requiring a ClubMembership here would
    # reject a perfectly normal reporter. reported_by is also never a form field
    # anywhere (BugReportForm/BugAdminForm/BugNoteForm don't expose it), so there
    # is no user-input path this would actually be guarding against.

    @property
    def is_open(self) -> bool:
        return self.status in (self.Status.SUBMITTED, self.Status.IN_PROGRESS)


class BugNote(UUIDModel):
    """A platform admin's free-text note on a bug -- added only from the control panel
    (bugs.services.reports.add_note), but shown back to the reporter on every surface:
    unlike priority/reported_by/club, notes are the one internal-looking field that is
    deliberately NOT admin-only -- see BugReport's own docstring."""

    bug = models.ForeignKey(BugReport, on_delete=models.CASCADE, related_name="notes", verbose_name=_("bug report"))
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("author"))
    body = models.TextField(_("note"))

    class Meta:
        verbose_name = _("bug note")
        verbose_name_plural = _("bug notes")
        ordering = ["created"]

    def __str__(self):
        return f"{self.bug} — {self.created:%Y-%m-%d}"
