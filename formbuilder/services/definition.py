"""Defining a form: creating it with its first questions, adding / retiring / deleting / ordering questions.

Shared by the ``/manage/forms`` screens (management.views) and the management API
(api/panel/routers/forms_admin.py), so the two cannot drift. Validation stays with the callers'
Django forms (management.forms.FormForm / FormBuilderFieldForm); this module only writes.
"""

from django.db import transaction
from django.db.models import ProtectedError

from formbuilder.models import Field, Form


@transaction.atomic
def create_form(club, form, rows) -> Form:
    """Save a valid ``FormForm`` for ``club`` together with its first questions.

    ``rows`` are dicts with ``label``, ``field_type``, ``required``, ``options`` and optionally
    ``help_text`` / ``section``; a row without a label is skipped (the web's unfilled spare rows).
    One ``.create()`` per row, never ``bulk_create()``: ``Field.save()`` derives the blank ``key``
    from the label, and without a key every question would collapse onto the same dynamic form field."""
    form_obj = form.save(commit=False)
    form_obj.club = club
    form_obj.save()
    for index, row in enumerate(rows):
        if not row.get("label"):
            continue
        Field.objects.create(
            form=form_obj,
            label=row["label"],
            field_type=row["field_type"],
            required=row["required"],
            options=row["options"],
            help_text=row.get("help_text", ""),
            section=row.get("section", ""),
            order=index,
        )
    return form_obj


def append_field(form_obj: Form, field_form, section: str | None = None) -> Field:
    """Save a valid ``FormBuilderFieldForm`` as the last question of ``form_obj``."""
    last_order = form_obj.fields.order_by("-order").values_list("order", flat=True).first()
    field_form.instance.form = form_obj
    field_form.instance.order = (last_order or 0) + 1
    if section is not None:
        field_form.instance.section = section
    return field_form.save()


def set_field_active(field: Field, active: bool) -> Field:
    """Retire (or bring back) a question. Answers keep their question, so this is the web's only way to remove one."""
    field.is_active = active
    field.save(update_fields=["is_active"])
    return field


def delete_field(field: Field) -> bool:
    """Hard-delete a question nobody has answered. False when answers exist (``Answer.field`` is PROTECT): retire it instead."""
    if field.answers.exists():
        return False
    field.delete()
    return True


def delete_form(form_obj: Form) -> bool:
    """Delete a form that was never sent and backs no evaluation checklist. False otherwise (its sends and responses stay)."""
    if form_obj.sends.exists():
        return False
    try:
        with transaction.atomic():
            form_obj.delete()
    except ProtectedError:
        return False
    return True


@transaction.atomic
def reorder_fields(form_obj: Form, field_ids) -> bool:
    """Give the form's questions the order of ``field_ids``. False unless it names exactly the form's questions, each once."""
    ids = [str(pk) for pk in field_ids]
    fields = {str(field.pk): field for field in form_obj.fields.all()}
    if len(ids) != len(set(ids)) or set(ids) != set(fields):
        return False
    for order, pk in enumerate(ids):
        if fields[pk].order != order:
            fields[pk].order = order
            fields[pk].save(update_fields=["order"])
    return True
