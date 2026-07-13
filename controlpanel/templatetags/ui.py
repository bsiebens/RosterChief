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
