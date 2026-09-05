from django import forms
from django.utils.translation import gettext_lazy as _

from .models import BugNote, BugReport


class BugReportForm(forms.ModelForm):
    """What a reporter fills in from management or mobile -- everything else on
    BugReport (club, reported_by, priority, status) is set by bugs.services.file_report,
    never by the reporter themselves."""

    class Meta:
        model = BugReport
        fields = ["title", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 5})}
        labels = {"title": _("Title"), "description": _("What went wrong?")}


class BugAdminForm(forms.ModelForm):
    """Control-panel-only edit -- status, priority and the fix version/date. Applied
    through bugs.services.update_bug, not saved directly, so fixed_at stays derived."""

    class Meta:
        model = BugReport
        fields = ["status", "priority", "fixed_version"]
        labels = {"fixed_version": _("Fixed in version")}


class BugNoteForm(forms.ModelForm):
    class Meta:
        model = BugNote
        fields = ["body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 3})}
        labels = {"body": _("Note")}
