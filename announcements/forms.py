from django import forms
from django.utils.translation import gettext_lazy as _

from club.models import Club

from .models import Announcement


class AnnouncementComposeForm(forms.ModelForm):
    """Step 1 of the compose flow (controlpanel.views.AnnouncementComposeView) --
    validated and re-shown as a preview, never saved directly. See
    announcements.services.create_and_confirm's own docstring for why the actual
    Announcement row is only created once a superuser confirms that preview."""

    class Meta:
        model = Announcement
        fields = ["title", "message", "club", "scheduled_for"]
        widgets = {
            "message": forms.Textarea(attrs={"rows": 5}),
            "scheduled_for": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }
        labels = {"club": _("Club")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["club"].required = False
        self.fields["club"].queryset = Club.objects.active().order_by("name")
        self.fields["club"].empty_label = _("All clubs")
        self.fields["scheduled_for"].required = False
