"""Template helpers for the daisyUI-based UI."""

from django import forms, template
from django.forms import BoundField

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
    "debug": ("bug", "Debug", "alert-info border-info"),
    "info": ("info", "Heads up", "alert-info border-info"),
    "success": ("circle-check", "Done", "alert-success border-success"),
    "warning": ("triangle-alert", "Careful", "alert-warning border-warning"),
    "error": ("circle-x", "Something went wrong", "alert-error border-error"),
}
DEFAULT_MESSAGE_ALERT = MESSAGE_ALERTS["info"]


@register.filter
def as_alert(message):
    """Presentation for one Django message: icon, bold title, body, colour.

    Django messages carry a level and a string — there is no title field — so the
    title comes from the level, unless the message carries one as ``extra_tags``.
    Call sites queue messages with ``notify`` (controlpanel/messages.py), which sets
    exactly that from a compact ``"<level>|<title>|<body>"`` spec::

        notify(request, f"s|Club created|{club} is live.")

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
def as_otp(field):
    """Render the code input for the boxed otp layout.

    No daisy classes — the wrapping div carries them — and no placeholder: allauth sets
    one ("Code"), and grey text sitting inside the boxes reads as an already-typed code.
    The label lives outside the box as an sr-only element, so nothing is lost.

    False, not a pop: as_widget() merges the widget's own attrs back in, so deleting the
    placeholder from a copy achieves nothing. Django's attribute template omits any attr
    whose value is False, which is the only way to actually suppress one.
    """
    return field.as_widget(attrs={"class": "", "placeholder": False})


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
def dom_id(pk, prefix):
    """A stable per-row DOM id, e.g. ``{{ due.pk|dom_id:"due_pay_modal" }}``.

    Django templates cannot concatenate a string literal with a non-string filter
    argument directly (``"prefix_"|add:some_uuid`` raises), which is what a per-row modal
    id needs. This is the one place that builds one, so every call site reads the same way.
    """
    return f"{prefix}_{pk}"


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


@register.inclusion_tag("templatetags/field.html")
def form_field(
    field: BoundField,
    label: str | None = None,
    help_text: str | None = None,
    show_label: bool = True,
    show_help_text: bool = True,
    show_placeholder: bool = True,
    show_as_toggle: bool = False,
    size: str = "full",
) -> dict:
    if label is not None:
        field.label = label

    if help_text is not None:
        field.help_text = help_text

    field_type = None
    match field.widget_type:
        case "select" | "nullbooleanselect" | "radioselect":
            field_type = "select"
        case "checkbox":
            field_type = "checkbox"
        case "textarea" | "markdownx":
            field_type = "textarea"
        case "clearablefile" | "multiplefile":
            field_type = "file"
        case "selectmultiple":
            field_type = "select"
        case _:
            field_type = "input"

    size_modifier = None
    match size:
        case "extra-small":
            size_modifier = "xs"
        case "small":
            size_modifier = "sm"
        case _:
            pass

    # if field.widget_type == "number":
    #    field.value = str(field.value())

    return {
        "field": field,
        "show_label": show_label,
        "show_help_text": show_help_text,
        "show_placeholder": show_placeholder,
        "show_as_toggle": show_as_toggle,
        "field_type": field_type,
        "size_modifier": size_modifier,
    }
