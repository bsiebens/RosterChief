from django import forms
from django.core import signing
from django.utils.translation import gettext_lazy as _

from .models import ContactRequest

#: Anti-spam without a CAPTCHA (see WEBSITE.md, "The contact form must be built
#: server-side"): the honeypot field below must arrive blank, and this signed,
#: timestamped token -- freshly issued on every GET -- must not be older than
#: an hour, which rules out a cached page or a replayed request.
TIMESTAMP_SALT = "marketing.contact"
MAX_AGE_SECONDS = 3600


def fresh_timestamp_token() -> str:
    return signing.dumps("marketing-contact", salt=TIMESTAMP_SALT)


class ContactForm(forms.Form):
    """The "Book a demo" form on the marketing homepage (marketing/views.py:home).

    Field set and copy match design_handoff_rosterchief_platform/WEBSITE.md's
    Contact section table exactly.
    """

    name = forms.CharField(label=_("Your name"), max_length=150, widget=forms.TextInput(attrs={"placeholder": "Bernard Siebens"}))
    email = forms.EmailField(label=_("Email"), widget=forms.EmailInput(attrs={"placeholder": "you@club.be"}))
    club = forms.CharField(label=_("Club"), max_length=150, widget=forms.TextInput(attrs={"placeholder": "Sharks Mechelen"}))
    members = forms.IntegerField(label=_("Members, roughly"), required=False, min_value=0, widget=forms.TextInput(attrs={"placeholder": "312", "inputmode": "numeric", "class": "font-mono"}))
    role = forms.ChoiceField(label=_("Your role"), choices=ContactRequest.Role.choices)
    message = forms.CharField(
        label=_("What is hurting most right now?"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": _("We chase attendance in six WhatsApp groups and the licence list lives in one person's inbox.")}),
    )

    # Carried silently from the CTA that was clicked (?plan=club|large|website) --
    # see WEBSITE.md item 6. Not shown to the visitor.
    plan = forms.CharField(required=False, widget=forms.HiddenInput)

    # Honeypot: real visitors never see or fill this (hidden off-screen in the
    # template); a bot filling every field trips it.
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    ts = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        widget_classes = {forms.Select: "select", forms.Textarea: "textarea"}
        for name, field in self.fields.items():
            if name in ("plan", "website", "ts"):
                continue
            component_class = next((cls for widget_type, cls in widget_classes.items() if isinstance(field.widget, widget_type)), "input")
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {component_class}".strip()

    def clean_website(self):
        value = self.cleaned_data["website"]
        if value:
            raise forms.ValidationError(_("Something went wrong. Please try again."))
        return value

    def clean_ts(self):
        value = self.cleaned_data["ts"]
        try:
            issued_at = signing.loads(value, salt=TIMESTAMP_SALT, max_age=MAX_AGE_SECONDS)
        except signing.BadSignature as exc:
            raise forms.ValidationError(_("Something went wrong. Please try again.")) from exc
        if issued_at != "marketing-contact":
            raise forms.ValidationError(_("Something went wrong. Please try again."))
        return value
