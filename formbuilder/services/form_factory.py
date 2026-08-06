"""Build a live ``django.forms.Form`` class from a Form instance's fields."""

from django import forms

from formbuilder.models import Field

from .options import field_choices

FIELD_BUILDERS = {
    Field.FieldType.TEXT: lambda field, kw: forms.CharField(**kw),
    Field.FieldType.TEXTAREA: lambda field, kw: forms.CharField(widget=forms.Textarea, **kw),
    Field.FieldType.NUMBER: lambda field, kw: forms.DecimalField(**kw),
    Field.FieldType.EMAIL: lambda field, kw: forms.EmailField(**kw),
    Field.FieldType.DATE: lambda field, kw: forms.DateField(**kw),
    Field.FieldType.CHOICE: lambda field, kw: forms.ChoiceField(choices=field_choices(field), **kw),
    Field.FieldType.MULTICHOICE: lambda field, kw: forms.MultipleChoiceField(choices=field_choices(field), **kw),
    Field.FieldType.CHECKBOX: lambda field, kw: forms.BooleanField(**kw),
    Field.FieldType.FILE: lambda field, kw: forms.FileField(**kw),
}


def _build_field(field):
    kwargs = {"required": field.required, "label": field.label, "help_text": field.help_text}
    return FIELD_BUILDERS[field.field_type](field, kwargs)


def build_form_class(form):
    """Return a ``forms.Form`` subclass with a field per active Field, keyed by ``key``."""
    fields = {field.key: _build_field(field) for field in form.fields.filter(is_active=True).order_by("order")}
    return type("DynamicForm", (forms.Form,), fields)


def build_form(form, data=None, files=None):
    """Instantiate the dynamic form, bound to ``data``/``files`` when provided."""
    return build_form_class(form)(data=data, files=files)
