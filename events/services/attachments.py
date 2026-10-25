"""Files attached to an event (events.models.EventAttachment): the upload rules, who may open
them, and the streaming response -- shared by the management site and the member/coach app so
the three surfaces can't drift apart."""

import os

from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext_lazy as _

from club.services.access import groups_manageable_by, is_club_admin, teams_managed_by, teams_staffed_by
from events.models import Attendance, EventAttachment

#: Per file, and per event -- generous for a schedule/map/consent form, small enough that one
#: event can't quietly fill the private volume.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_EVENT = 20

#: Documents and pictures only. Deliberately no HTML/SVG/scripts: a download is always sent as an
#: attachment with a sandboxing CSP, but there is no reason to accept what nobody should open.
ALLOWED_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".txt", ".csv", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".ics"})


def validate_attachment_file(uploaded) -> None:
    extension = os.path.splitext(uploaded.name)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError(_("“%(name)s” is not an allowed file type.") % {"name": os.path.basename(uploaded.name)})
    if uploaded.size > MAX_ATTACHMENT_BYTES:
        raise ValidationError(_("“%(name)s” is larger than %(limit)s MB.") % {"name": os.path.basename(uploaded.name), "limit": MAX_ATTACHMENT_BYTES // (1024 * 1024)})


def add_attachments(event, uploaded_files, member) -> list[EventAttachment]:
    """Store ``uploaded_files`` on ``event``. Raises ValidationError, creating nothing, when they
    would take the event past MAX_ATTACHMENTS_PER_EVENT."""
    if event.attachments.count() + len(uploaded_files) > MAX_ATTACHMENTS_PER_EVENT:
        raise ValidationError(_("An event can have at most %(limit)s attachments.") % {"limit": MAX_ATTACHMENTS_PER_EVENT})
    return [EventAttachment.objects.create(event=event, file=uploaded, name=os.path.basename(uploaded.name)[:255], size=uploaded.size, uploaded_by=member) for uploaded in uploaded_files]


def can_view_attachments(user, club, event, managed_people=()) -> bool:
    """Whoever runs the event (admin, manager of one of its teams, member of one of its groups),
    staff of one of its teams, or an invited person's account -- the member themself or their
    parent (``managed_people``: the signed-in account's own member plus the children they manage)."""
    if is_club_admin(user, club):
        return True
    team_ids = list(event.teams.values_list("pk", flat=True))
    if team_ids and (teams_managed_by(user, club).filter(pk__in=team_ids).exists() or teams_staffed_by(user, club).filter(pk__in=team_ids).exists()):
        return True
    if groups_manageable_by(user, club).filter(pk__in=event.groups.values_list("pk", flat=True)).exists():
        return True
    people = [person.pk for person in managed_people]
    return bool(people) and Attendance.objects.filter(event=event, member_id__in=people).exists()


def attachment_response(attachment) -> FileResponse:
    """Stream the file from private storage -- always as a download, never rendered in the page."""
    try:
        handle = attachment.file.open("rb")
    except FileNotFoundError as error:
        raise Http404("No such file.") from error
    response = FileResponse(handle, as_attachment=True, filename=attachment.name, content_type="application/octet-stream")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    patch_vary_headers(response, ["Cookie"])
    return response
