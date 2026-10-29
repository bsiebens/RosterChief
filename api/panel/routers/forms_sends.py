"""Management API: sending a form to an audience (API.md §17, phase C leftover).

The JSON counterpart of ``FormSendCreateView`` / ``FormSendUpdateView`` and the sends list of
``FormDetailView`` (management/views.py):

* ``GET /manage/forms/{form_id}/sends``: the form's sends, newest first;
* ``POST /manage/forms/{form_id}/sends``: send the form (audience, window, public link); the audience is
  notified in the background, exactly like the web (``formbuilder.services.sends.create_send``);
* ``POST .../sends/preview``: who a draft send would address, nothing is saved;
* ``GET|PATCH .../sends/{send_id}``: one send with audience counts; edit it (``is_active: false`` pauses it);
* ``GET .../sends/{send_id}/audience``: the members it addresses.

The send-level report, pending list and CSV are in ``forms_submissions``. The web has no delete, no
manual reminder (``formbuilder.tasks.send_form_reminders`` is a scheduled job) and no per-form
evaluation-checklist link on these screens, so none are exposed.

Gate (the web's ``FormManagerRequiredMixin``): ``can_manage_forms`` and the ``forms`` module. Audience fields are
validated by management.forms.FormSendForm (non-admins are limited to their own teams and groups).
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
from api.panel.schemas import forms_sends as s
from api.protected.services import club_features
from club.services.access import can_manage_forms, is_club_admin
from formbuilder.models import Form, FormSend
from formbuilder.services.audience import is_send_open, members_not_yet_submitted
from formbuilder.services.sends import audience_preview, create_send, send_audience
from management.forms import FormSendForm
from members.models import Member

router = Router(tags=["panel: form sends"])

PREFIX = "/manage/forms/{uuid:form_id}/sends"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
MULTIPLE = {"team_ids": "teams", "group_ids": "groups", "invited_member_ids": "invited_members", "excluded_member_ids": "excluded_members"}
CHECKBOXES = ("club_wide", "is_active", "is_public")
ALIASES = {"teams": "team_ids", "groups": "group_ids", "invited_members": "invited_member_ids", "excluded_members": "excluded_member_ids"}


def _club(request):
    club = panel_club(request)
    require(request, club, can_manage_forms)
    if not club_features(club)["forms"]:
        raise ApiError(404, "feature_disabled", _("This feature isn't available for this club."))
    return club


def _form(club, form_id) -> Form:
    return get_object_or_404(Form, pk=form_id, club=club)


def _send(club, form_id, send_id) -> FormSend:
    return get_object_or_404(FormSend.objects.filter(club=club, form_id=form_id).select_related("form", "created_by").annotate(submission_count=Count("submissions", distinct=True)), pk=send_id)


def _out(send: FormSend) -> dict:
    return {
        "id": send.pk,
        "form_id": send.form_id,
        "team_ids": [team.pk for team in send.teams.all()],
        "group_ids": [group.pk for group in send.groups.all()],
        "club_wide": send.club_wide,
        "invited_member_ids": [member.pk for member in send.invited_members.all()],
        "excluded_member_ids": [member.pk for member in send.excluded_members.all()],
        "season_id": send.season_id,
        "opens_at": send.opens_at,
        "closes_at": send.closes_at,
        "max_submissions_per_user": send.max_submissions_per_user,
        "is_active": send.is_active,
        "is_open": is_send_open(send),
        "is_public": send.is_public,
        "public_token": send.public_token if send.is_public else None,
        "reminder_sent_at": send.reminder_sent_at,
        "created_by": str(send.created_by) if send.created_by else None,
        "created": send.created,
        "submission_count": send.submission_count if hasattr(send, "submission_count") else send.submissions.count(),
    }


def _detail(send: FormSend) -> dict:
    audience = len(send_audience(send))
    not_yet = members_not_yet_submitted(send).count()
    return {**_out(send), "audience_count": audience, "not_yet_count": not_yet}


def _current_values(send: FormSend) -> dict:
    return {
        "team_ids": [team.pk for team in send.teams.all()],
        "group_ids": [group.pk for group in send.groups.all()],
        "invited_member_ids": [member.pk for member in send.invited_members.all()],
        "excluded_member_ids": [member.pk for member in send.excluded_members.all()],
        "club_wide": send.club_wide,
        "opens_at": send.opens_at,
        "closes_at": send.closes_at,
        "max_submissions_per_user": send.max_submissions_per_user,
        "is_active": send.is_active,
        "is_public": send.is_public,
    }


def _django_form(request, club, values: dict, instance) -> FormSendForm:
    if values.get("club_wide") and not is_club_admin(request.user, club):
        raise ApiError(422, "validation_error", fields={"club_wide": [str(_("Only an admin can send a form to the whole club."))]})
    return FormSendForm(post_data(values, multiple=MULTIPLE, checkboxes=CHECKBOXES), club=club, user=request.user, instance=instance)


@router.get(PREFIX, response={200: s.SendListOut, **ERRORS}, summary="Sends of a form")
def list_sends(request, form_id: uuid.UUID):
    """Every time the form went out, newest first, with its audience definition, window and response count. Forms managers."""
    club = _club(request)
    form = _form(club, form_id)
    sends = form.sends.select_related("created_by").annotate(submission_count=Count("submissions", distinct=True)).prefetch_related("teams", "groups", "invited_members", "excluded_members").order_by("-created")
    return {"items": [_out(send) for send in sends]}


@router.post(PREFIX, response={201: s.SendDetailOut, **ERRORS, **INVALID}, summary="Send a form to an audience")
def create_send_endpoint(request, form_id: uuid.UUID, payload: s.SendIn):
    """Creates the send and notifies its audience in the background. Admins may address the whole club (`club_wide`);
    other managers must pick at least one of their own teams or groups (unless `is_public`). Forms managers."""
    club = _club(request)

    def work():
        form = _form(club, form_id)
        django_form = _django_form(request, club, payload.dict(), FormSend(club=club, form=form))
        if not django_form.is_valid():
            raise form_error(django_form, ALIASES)
        send = create_send(django_form, Member.objects.filter(user=request.user).first())
        return 201, _detail(_send(club, form_id, send.pk))

    return idempotent(request, work)


@router.post(PREFIX + "/preview", response={200: s.AudienceOut, **ERRORS, **INVALID}, summary="Preview who a send would address")
def preview_send(request, form_id: uuid.UUID, payload: s.SendIn):
    """Same body and validation as creating a send, but nothing is saved or notified; returns the members it would address. Forms managers."""
    club = _club(request)
    form = _form(club, form_id)
    django_form = _django_form(request, club, payload.dict(), FormSend(club=club, form=form))
    if not django_form.is_valid():
        raise form_error(django_form, ALIASES)
    members = audience_preview(django_form)
    return {"count": len(members), "members": [{"id": member.pk, "name": str(member)} for member in members]}


@router.get(PREFIX + "/{uuid:send_id}", response={200: s.SendDetailOut, **ERRORS}, summary="One send")
def get_send(request, form_id: uuid.UUID, send_id: uuid.UUID):
    """The send with the size of its audience and how many have not responded yet. Forms managers."""
    club = _club(request)
    return _detail(_send(club, form_id, send_id))


@router.patch(PREFIX + "/{uuid:send_id}", response={200: s.SendDetailOut, **ERRORS, **INVALID}, summary="Edit a send")
def update_send(request, form_id: uuid.UUID, send_id: uuid.UUID, payload: s.SendPatch):
    """Send only the fields to change (the audience lists replace the old ones). `is_active: false` pauses collection.
    Editing does not notify the audience again. Forms managers."""
    club = _club(request)
    send = _send(club, form_id, send_id)
    values = {**_current_values(send), **payload.dict(exclude_unset=True)}
    django_form = _django_form(request, club, values, FormSend.objects.get(pk=send.pk))
    if not django_form.is_valid():
        raise form_error(django_form, ALIASES)
    django_form.save()
    return _detail(_send(club, form_id, send_id))


@router.get(PREFIX + "/{uuid:send_id}/audience", response={200: s.AudienceOut, **ERRORS}, summary="Members a send addresses")
def send_audience_endpoint(request, form_id: uuid.UUID, send_id: uuid.UUID):
    """Everyone the send is addressed to (teams of its season, groups, invited, minus excluded), by name. Forms managers."""
    club = _club(request)
    members = send_audience(_send(club, form_id, send_id))
    return {"count": len(members), "members": [{"id": member.pk, "name": str(member)} for member in members]}
