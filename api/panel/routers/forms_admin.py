"""Management API: form definitions and their questions (API.md §17, phase C).

The JSON counterpart of the definition side of ``/manage/forms`` (management.views.Form*View): the
forms list, create (a form with its first questions), detail, edit, and add / edit / retire /
delete / reorder a question. Sends and responses are other slices.

Who may do what is the web's rule (``FormManagerRequiredMixin``): ``can_manage_forms``
(admin, editor or coach manager), and the club needs the ``forms`` module (``404 feature_disabled``
without it). Validation is management.forms.FormForm / FormBuilderFieldForm; writing is
formbuilder.services.definition, which the web views call as well.

Beyond the web, which has neither: ``DELETE`` of a form (409 ``form_in_use`` once it was sent or
backs an evaluation checklist), ``DELETE`` of a question nobody answered (409 ``field_in_use``,
retire it with ``is_active: false`` instead), and ``POST .../fields/reorder``. The web's
"publish / archive" is the form's ``is_active``; the model has no sections beyond a question's
``section`` heading, no conditional rules and no duplicate action, so none are exposed.
"""

import uuid

from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.forms import form_error, post_data
from api.core.idempotency import idempotent
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas import forms_admin as s
from api.protected.services import club_features
from club.services.access import can_manage_forms
from formbuilder.models import Field as FormField
from formbuilder.models import Form
from formbuilder.services.definition import append_field, create_form, delete_field, delete_form, reorder_fields
from formbuilder.services.options import field_choices
from management.forms import FormBuilderFieldForm, FormForm

router = Router(tags=["panel: forms"])

PREFIX = "/manage/forms"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
CONFLICT = {409: ErrorOut}
FORM_CHECKBOXES = ("login_required", "is_active")


# --- helpers -----------------------------------------------------------------------------------


def _club(request):
    """The club on this host for a forms manager, with the ``forms`` module on."""
    club = panel_club(request)
    require(request, club, can_manage_forms)
    if not club_features(club)["forms"]:
        raise ApiError(404, "feature_disabled", _("This feature isn't available for this club."))
    return club


def _forms(club):
    return Form.objects.filter(club=club).annotate(send_count=Count("sends", distinct=True))


def _form(club, form_id) -> Form:
    return get_object_or_404(_forms(club), pk=form_id)


def _field(form: Form, field_id) -> FormField:
    return get_object_or_404(form.fields.all(), pk=field_id)


def _field_out(field: FormField) -> dict:
    return {
        "id": field.pk,
        "key": field.key,
        "label": field.label,
        "field_type": field.field_type,
        "required": field.required,
        "help_text": field.help_text,
        "section": field.section,
        "order": field.order,
        "is_active": field.is_active,
        "options": [str(value) for value, _label in field_choices(field)],
    }


def _form_out(form: Form) -> dict:
    return {
        "id": form.pk,
        "title": form.title,
        "slug": form.slug,
        "description": form.description,
        "is_active": form.is_active,
        "login_required": form.login_required,
        "send_count": form.send_count,
        "created": form.created,
    }


def _detail_out(form: Form) -> dict:
    return {**_form_out(form), "fields": [_field_out(field) for field in form.fields.order_by("order", "pk")]}


def _field_data(values: dict, order: int = 0) -> dict:
    """A question's JSON -> what the web modal posts (``options`` is one choice per line there)."""
    data = post_data({key: values.get(key) for key in ("label", "field_type", "help_text")}, checkboxes=())
    data["order"] = order
    data["options"] = "\n".join(values.get("options") or [])
    if values.get("required"):
        data["required"] = "on"
    return data


def _field_form(values: dict, order: int = 0, instance=None) -> FormBuilderFieldForm:
    return FormBuilderFieldForm(_field_data(values, order), instance=instance)


# --- forms -------------------------------------------------------------------------------------


@router.get(PREFIX, response={200: s.FormListOut, **ERRORS}, summary="Forms")
def list_forms(request, is_active: bool | None = None):
    """The club's forms by title, with how often each was sent; `is_active` filters archived or live ones. Forms managers."""
    club = _club(request)
    forms = _forms(club)
    if is_active is not None:
        forms = forms.filter(is_active=is_active)
    return {"items": [_form_out(form) for form in forms]}


@router.post(PREFIX, response={201: s.FormDetailOut, **ERRORS, **INVALID}, summary="Create a form with its first questions")
def create_form_endpoint(request, payload: s.FormIn):
    """`fields` are the first questions (optional; add more later). Sending the form to an audience is a separate step. Forms managers."""
    club = _club(request)

    def work():
        form = FormForm(post_data(payload.dict(exclude={"fields"}), checkboxes=FORM_CHECKBOXES), instance=Form(club=club))
        errors: dict[str, list[str]] = {}
        if not form.is_valid():
            errors.update(form_error(form).fields)
        rows, seen = [], set()
        for index, item in enumerate(payload.fields):
            values = item.dict()
            field_form = _field_form(values, order=index)
            if not field_form.is_valid():
                errors.update({f"fields.{index}.{name}": [str(m) for m in messages] for name, messages in field_form.errors.items()})
                continue
            label = field_form.cleaned_data["label"].strip().lower()
            if label in seen:
                errors[f"fields.{index}.label"] = [str(_("This question is listed twice."))]
            seen.add(label)
            rows.append({**values, "options": field_form.cleaned_data["options"]})
        if errors:
            raise ApiError(422, "validation_error", fields=errors)
        return 201, _detail_out(_form(club, create_form(club, form, rows).pk))

    return idempotent(request, work)


@router.get(PREFIX + "/{uuid:form_id}", response={200: s.FormDetailOut, **ERRORS}, summary="One form with its questions")
def get_form(request, form_id: uuid.UUID):
    return _detail_out(_form(_club(request), form_id))


@router.patch(PREFIX + "/{uuid:form_id}", response={200: s.FormDetailOut, **ERRORS, **INVALID}, summary="Edit a form")
def update_form(request, form_id: uuid.UUID, payload: s.FormPatch):
    """Send only the fields to change. `is_active: false` archives the form (it can no longer be picked for a new send), `true` publishes it again. Forms managers."""
    club = _club(request)
    form = _form(club, form_id)
    values = {"title": form.title, "description": form.description, "login_required": form.login_required, "is_active": form.is_active, **payload.dict(exclude_unset=True)}
    django_form = FormForm(post_data(values, checkboxes=FORM_CHECKBOXES), instance=form)
    if not django_form.is_valid():
        raise form_error(django_form)
    django_form.save()
    return _detail_out(_form(club, form_id))


@router.delete(PREFIX + "/{uuid:form_id}", response={204: None, **ERRORS, **CONFLICT}, summary="Delete a form")
def remove_form(request, form_id: uuid.UUID):
    """409 `form_in_use` once the form was sent (archive it with `is_active: false`) or backs an evaluation checklist. Forms managers."""
    club = _club(request)
    if not delete_form(_form(club, form_id)):
        raise ApiError(409, "form_in_use", _("This form was already sent or is in use, and can't be deleted. Archive it instead."))
    return 204, None


# --- questions ---------------------------------------------------------------------------------


@router.post(PREFIX + "/{uuid:form_id}/fields", response={201: s.FormFieldOut, **ERRORS, **INVALID}, summary="Add a question")
def add_field(request, form_id: uuid.UUID, payload: s.FieldIn):
    """Appended after the last question; move it with `PATCH .../fields/{id}` (`order`) or `POST .../fields/reorder`. Forms managers."""
    club = _club(request)
    form = _form(club, form_id)

    def work():
        values = payload.dict()
        field_form = _field_form(values)
        if not field_form.is_valid():
            raise form_error(field_form)
        return 201, _field_out(append_field(form, field_form, section=values["section"]))

    return idempotent(request, work)


@router.post(PREFIX + "/{uuid:form_id}/fields/reorder", response={200: s.FormDetailOut, **ERRORS, **INVALID}, summary="Reorder the questions")
def reorder(request, form_id: uuid.UUID, payload: s.FieldOrderIn):
    """`field_ids` must name every question of the form (retired ones too) exactly once. Forms managers."""
    club = _club(request)
    form = _form(club, form_id)
    if not reorder_fields(form, payload.field_ids):
        raise ApiError(422, "validation_error", fields={"field_ids": [str(_("List every question of this form exactly once."))]})
    return _detail_out(_form(club, form_id))


@router.patch(PREFIX + "/{uuid:form_id}/fields/{uuid:field_id}", response={200: s.FormFieldOut, **ERRORS, **INVALID}, summary="Edit a question")
def update_field(request, form_id: uuid.UUID, field_id: uuid.UUID, payload: s.FieldPatch):
    """Send only the fields to change. The `key` never changes, so answers stay attached; editing an answered question is allowed. Forms managers."""
    club = _club(request)
    form = _form(club, form_id)
    field = _field(form, field_id)
    changes = payload.dict(exclude_unset=True)
    original_options = field.options
    values = {**_field_out(field), **{key: value for key, value in changes.items() if key not in ("order", "is_active") and value is not None}}
    order = changes["order"] if changes.get("order") is not None else field.order
    field_form = _field_form(values, order=order, instance=field)
    if not field_form.is_valid():
        raise form_error(field_form)
    field = field_form.save(commit=False)
    if changes.get("options") is None:
        field.options = original_options  # untouched choices keep any label/value pairs the web form would flatten
    if changes.get("section") is not None:
        field.section = changes["section"]
    if changes.get("is_active") is not None:
        field.is_active = changes["is_active"]
    field.save()
    return _field_out(field)


@router.delete(PREFIX + "/{uuid:form_id}/fields/{uuid:field_id}", response={204: None, **ERRORS, **CONFLICT}, summary="Delete a question")
def remove_field(request, form_id: uuid.UUID, field_id: uuid.UUID):
    """409 `field_in_use` once the question has answers; retire it with `PATCH` `is_active: false` instead. Forms managers."""
    club = _club(request)
    field = _field(_form(club, form_id), field_id)
    if not delete_field(field):
        raise ApiError(409, "field_in_use", _("This question already has answers and can't be deleted. Deactivate it instead."))
    return 204, None
