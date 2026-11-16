"""Files attached to an event (a tournament schedule, a map, a consent form), stored in private storage.

Who may do what
* **See** the list and **open** a file: ``tasks.can_view_event_extras`` -- everyone in the management app, plus the
  event's audience (an account whose own member, or one of whose children, has an attendance row on the event).
* **Upload and delete**: ``access.can_manage_attendance`` -- settings holders and managers of any of the event's teams.

Upload rules (as the old app): documents and pictures only (``ALLOWED_EXTENSIONS``, deliberately no HTML/SVG/scripts),
at most ``MAX_ATTACHMENT_BYTES`` per file and ``MAX_ATTACHMENTS_PER_EVENT`` per event, no empty files. The stored name
is the uploaded base name (any client-side directory part dropped, control characters removed); the storage path is
made filesystem-safe and unguessable by ``events.models.event_attachment_path``. A batch is all or nothing.

Files leave storage when their row is deleted (``delete_attachment``, or the event being deleted: the ``post_delete``
receiver below), after the transaction commits so a rollback never loses a file still referenced. Downloads must be
streamed as an attachment (never inline) with ``DOWNLOAD_HEADERS``; ``open_attachment`` gives the open file.
The receiver is connected when this module is imported (events/signals.py should import it so event deletes outside
the API also clean up). A failure inside ``add_attachments`` removes the files it wrote; a rollback of an OUTER
transaction after it returned can still leave orphan files (Django has no on-rollback hook).
"""

from __future__ import annotations

import os
import unicodedata

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from events.models import Event, EventAttachment
from events.services.tasks import _actor_member, can_view_event_extras
from members.services import access, audit
from members.services.errors import Invalid, NotAllowed

#: Per file, and per event -- generous for a schedule/map/consent form, small enough that one event can't quietly fill
#: the private volume.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_EVENT = 20

ALLOWED_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".txt", ".csv", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".ics"})

#: Headers for a download response: always a download, never sniffed or rendered, never cached by a shared cache.
DOWNLOAD_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}
DOWNLOAD_CONTENT_TYPE = "application/octet-stream"

AUDIT_FIELDS = ("name", "size")


def can_manage_attachments(user, event: Event) -> bool:
    return access.can_manage_attendance(user, event)


def require_attachment_manager(actor, event: Event) -> None:
    if not can_manage_attachments(actor, event):
        raise NotAllowed("You are not allowed to manage the attachments of this event.")


def require_view(actor, event: Event) -> None:
    if not can_view_event_extras(actor, event):
        raise NotAllowed("You are not allowed to see this event's attachments.")


# --- validation ----------------------------------------------------------------------------------------------------


def display_name(raw_name: str) -> str:
    """The name a file is shown and downloaded as: the base name only (``/`` and ``\\`` directory parts dropped),
    control characters removed, surrounding whitespace trimmed, at most 255 characters (keeping the extension)."""
    name = (raw_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    if not name.strip("."):
        return ""
    if len(name) > 255:
        stem, extension = os.path.splitext(name)
        name = stem[: 255 - len(extension)] + extension
    return name


def validate_attachment_file(uploaded) -> str:
    """Raise ``Invalid`` (field ``files``) unless ``uploaded`` is an allowed, non-empty file within the size limit.
    Returns its display name."""
    name = display_name(getattr(uploaded, "name", ""))
    stem, extension = os.path.splitext(name)
    if not stem or not name:
        raise Invalid("The file has no usable name.", {"files": ["The file has no usable name."]})
    if extension.lower() not in ALLOWED_EXTENSIONS:
        raise Invalid(f"“{name}” is not an allowed file type.", {"files": [f"“{name}” is not an allowed file type."]})
    size = uploaded.size or 0
    if size <= 0:
        raise Invalid(f"“{name}” is empty.", {"files": [f"“{name}” is empty."]})
    if size > MAX_ATTACHMENT_BYTES:
        message = f"“{name}” is larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB."
        raise Invalid(message, {"files": [message]})
    return name


# --- reading -------------------------------------------------------------------------------------------------------


def list_attachments(actor, event: Event) -> list[EventAttachment]:
    require_view(actor, event)
    return list(event.attachments.select_related("uploaded_by").order_by("created"))


def open_attachment(attachment: EventAttachment, *, actor=None):
    """The attachment's file, opened for reading in binary mode, for streaming (the caller closes it; FileResponse
    does). Stream it with ``filename=attachment.name``, ``as_attachment=True``, ``content_type=DOWNLOAD_CONTENT_TYPE``
    and ``DOWNLOAD_HEADERS``. With ``actor`` the view rule is checked first (``NotAllowed``). Raises
    ``FileNotFoundError`` when the file is gone from storage (map it to 404)."""
    if actor is not None:
        require_view(actor, attachment.event)
    if not attachment.file or not attachment.file.storage.exists(attachment.file.name):
        raise FileNotFoundError(attachment.file.name or "")
    return attachment.file.storage.open(attachment.file.name, "rb")


# --- writing -------------------------------------------------------------------------------------------------------


@transaction.atomic
def add_attachments(actor, event: Event, uploaded_files) -> list[EventAttachment]:
    """Store ``uploaded_files`` on ``event``. All or nothing: any invalid file, or going past
    ``MAX_ATTACHMENTS_PER_EVENT``, raises ``Invalid`` and stores nothing."""
    require_attachment_manager(actor, event)
    uploaded_files = list(uploaded_files)
    if not uploaded_files:
        raise Invalid("Choose at least one file.", {"files": ["Choose at least one file."]})
    Event.objects.select_for_update().filter(pk=event.pk).first()  # serialise concurrent uploads for the count below
    if event.attachments.count() + len(uploaded_files) > MAX_ATTACHMENTS_PER_EVENT:
        message = f"An event can have at most {MAX_ATTACHMENTS_PER_EVENT} attachments."
        raise Invalid(message, {"files": [message]})
    names = [validate_attachment_file(uploaded) for uploaded in uploaded_files]
    uploader = _actor_member(actor)
    created: list[EventAttachment] = []
    attachment = None
    try:
        for uploaded, name in zip(uploaded_files, names, strict=True):
            attachment = EventAttachment(event=event, name=name, size=uploaded.size, uploaded_by=uploader)
            attachment.file.save(name, uploaded, save=False)
            attachment.save()
            created.append(attachment)
            audit.record(actor, "event.attachment_created", target=attachment, changes=audit.snapshot(attachment, AUDIT_FIELDS))
    except BaseException:
        # Files already written for this batch would otherwise be orphans once the rows roll back.
        stored = {a.file.name for a in created}
        if attachment is not None and attachment.file.name:
            stored.add(attachment.file.name)
        for name in stored:
            attachment.file.storage.delete(name)
        raise
    return created


@transaction.atomic
def delete_attachment(actor, attachment: EventAttachment) -> None:
    """Delete the row; the file leaves storage once the transaction commits (``post_delete`` receiver below)."""
    require_attachment_manager(actor, attachment.event)
    label = str(attachment)
    audit.record(actor, "event.attachment_deleted", target=attachment, target_label=label, changes=audit.snapshot(attachment, AUDIT_FIELDS, deleted=True))
    attachment.delete()


@receiver(post_delete, sender=EventAttachment, dispatch_uid="events.attachments.remove_file")
def remove_file_on_delete(sender, instance: EventAttachment, **kwargs) -> None:
    """Every deleted attachment (on its own, or with its event) takes its file along, once the deletion committed."""
    name = instance.file.name if instance.file else ""
    if not name:
        return
    storage = instance.file.storage
    transaction.on_commit(lambda: storage.delete(name))
