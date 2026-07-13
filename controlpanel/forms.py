from django import forms
from django.utils.translation import gettext_lazy as _

from club.models import Club

from .services.admins import find_member_by_email


class ClubForm(forms.ModelForm):
    class Meta:
        model = Club
        fields = ["name", "slug"]
        help_texts = {"slug": _("Drives the club's subdomain. Left blank, it is derived from the name.")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False


class ClubAdminForm(forms.Form):
    """Grant club-admin rights to an email address, creating the person if new."""

    email = forms.EmailField(label=_("Email address"), help_text=_("If this email has no account yet, one is created and they set a password via the reset link."))
    first_name = forms.CharField(label=_("First name"), required=False)
    last_name = forms.CharField(label=_("Last name"), required=False)

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")

        # Only a brand-new person needs a name; an existing member already has one.
        if email and find_member_by_email(email) is None:
            for field in ("first_name", "last_name"):
                if not cleaned.get(field):
                    self.add_error(field, _("Required: this email has no account yet."))

        return cleaned
