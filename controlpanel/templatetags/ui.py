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


@register.filter
def daisy(field):
    """Render a bound form field with the right daisyUI classes."""
    widget = field.field.widget
    css = next((css for widget_type, css in WIDGET_CLASSES if isinstance(widget, widget_type)), DEFAULT_WIDGET_CLASS)

    classes = [widget.attrs.get("class", ""), css]
    if field.errors:
        classes.append(f"{css.split()[0]}-error")

    attrs = dict(widget.attrs)
    attrs["class"] = " ".join(part for part in classes if part)
    return field.as_widget(attrs=attrs)
