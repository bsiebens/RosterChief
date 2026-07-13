"""Template helpers for the daisyUI-based UI."""

from django import forms, template

register = template.Library()

#: daisyUI class per widget kind. Django renders widgets unstyled, so this is
#: what makes every allauth and control-panel form field look right.
WIDGET_CLASSES = (
    (forms.CheckboxInput, "checkbox"),
    (forms.FileInput, "file-input file-input-bordered w-full"),
    (forms.RadioSelect, "radio"),
    (forms.Select, "select select-bordered w-full"),
    (forms.Textarea, "textarea textarea-bordered w-full"),
)
DEFAULT_WIDGET_CLASS = "input input-bordered w-full"


#: Icon, default heading and daisyUI colour per message level.
MESSAGE_ALERTS = {
    "debug": ("bug", "Debug", "alert-info"),
    "info": ("info", "Heads up", "alert-info"),
    "success": ("circle-check", "Done", "alert-success"),
    "warning": ("triangle-alert", "Careful", "alert-warning"),
    "error": ("circle-x", "Something went wrong", "alert-error"),
}
DEFAULT_MESSAGE_ALERT = MESSAGE_ALERTS["info"]


@register.filter
def as_alert(message):
    """Presentation for one Django message: icon, bold title, body, colour.

    Django messages carry a level and a string — there is no title field — so the
    title comes from the level, and a call site that wants a specific one passes it
    as ``extra_tags``::

        messages.success(request, f"{club} is live.", extra_tags="Club created")

    Keyed on ``level_tag``, never ``tags``: ``tags`` is extra_tags and level_tag
    joined, so a message carrying a custom title would stop matching its own level
    and quietly render as info.
    """
    icon, title, css = MESSAGE_ALERTS.get(message.level_tag, DEFAULT_MESSAGE_ALERT)

    return {"icon": icon, "title": message.extra_tags or title, "body": message.message, "css": css}


#: Icon shown inside the field, by form field name. Anything unlisted gets none.
FIELD_ICONS = {
    "login": "mail",
    "email": "mail",
    "email2": "mail",
    "oldpassword": "lock-keyhole",
    "password": "lock-keyhole",
    "password1": "lock-keyhole",
    "password2": "lock-keyhole",
    "code": "shield-check",
}


#: Fields rendered as a boxed one-time-code input rather than a plain text field.
OTP_FIELDS = {"code"}


@register.filter
def is_otp(field):
    return field.name in OTP_FIELDS


@register.filter
def field_icon(field):
    return FIELD_ICONS.get(field.name, "")


@register.filter
def excluded(field, names):
    """Is this field in a comma-separated exclude list?

    Split rather than a substring test: ``"password" in "password2"`` is true, and the
    login page excluding "remember" must not silently drop a field whose name happens
    to contain it.
    """
    return field.name in (names or "").split(",")


@register.filter
def daisy(field, css=None):
    """Render a bound form field with the right daisyUI classes.

    Pass ``css`` to override them — the icon-in-field layout wraps the input in a
    ``label.input``, and there the input itself must NOT carry the ``input`` class
    (daisyUI styles the wrapper instead), so it is rendered with ``grow``. The error
    state then belongs on the wrapper too, which is why an override skips it here.
    """
    widget = field.field.widget

    if css is None:
        css = next((css for widget_type, css in WIDGET_CLASSES if isinstance(widget, widget_type)), DEFAULT_WIDGET_CLASS)
        if field.errors:
            css = f"{css} {css.split()[0]}-error"

    attrs = dict(widget.attrs)
    attrs["class"] = " ".join(part for part in [widget.attrs.get("class", ""), css] if part)
    return field.as_widget(attrs=attrs)
