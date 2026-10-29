"""Multipart file uploads for the API (API.md §17.3).

Convention: a file is uploaded as ``multipart/form-data`` on the resource endpoint itself
(``POST /manage/teams/{id}/photo``), never through a separate "upload then reference" step. Use
ninja's ``File``/``UploadedFile`` for the part and ``validate_upload`` for the limits::

    from ninja import File, Form, UploadedFile
    from api.core.uploads import validate_upload, IMAGE_TYPES

    @router.post("/manage/teams/{team_id}/photo", response={200: TeamOut, **ERRORS, 413: ErrorOut, 415: ErrorOut, 422: ErrorOut})
    def set_photo(request, team_id: uuid.UUID, photo: File[UploadedFile], caption: Form[str] = ""):
        upload = validate_upload(photo, field="photo", content_types=IMAGE_TYPES, max_bytes=5 * 1024 * 1024)
        ...

Other form values travel as ``Form[...]`` parameters beside the file; the OpenAPI spec then
shows ``multipart/form-data``. Failures use the usual envelope:

* ``413 file_too_large`` -- over ``max_bytes``;
* ``415 unsupported_media_type`` -- content type (or extension) not accepted;
* ``422 validation_error`` with ``fields[<field>]`` -- empty file or a missing part.

Defaults come from ``settings.API_UPLOAD_MAX_BYTES`` / ``API_UPLOAD_CONTENT_TYPES``. The client's
declared content type is checked, and the extension too when ``extensions`` is given; whether the
bytes really are what they claim (e.g. Pillow opening an image) is the caller's concern.
"""

import os

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.template.defaultfilters import filesizeformat
from django.utils.translation import gettext as _

from api.core.errors import ApiError

IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
SPREADSHEET_TYPES = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)
PDF_TYPES = ("application/pdf",)
#: What clients send when they do not know the type (browsers and tools often do for .xlsx); see ``generic_ok``.
GENERIC_TYPES = ("", "application/octet-stream", "binary/octet-stream")


def validate_upload(
    upload: UploadedFile | None,
    *,
    field: str = "file",
    max_bytes: int | None = None,
    content_types=None,
    extensions=None,
    allow_empty: bool = False,
    generic_ok: bool = False,
) -> UploadedFile:
    """Return ``upload`` if it passes, else raise ``ApiError`` (413 / 415 / 422).

    ``content_types`` / ``extensions`` default to ``settings.API_UPLOAD_CONTENT_TYPES`` and no
    extension rule; pass ``content_types=()`` to accept any type. ``extensions`` are lower-case
    with the dot (``(".xlsx",)``). With ``generic_ok`` a generic declared type (empty or
    ``application/octet-stream``, see ``GENERIC_TYPES``) passes the type check and the ``extensions`` rule alone
    decides -- for formats clients commonly mislabel, e.g. ``content_types=SPREADSHEET_TYPES, extensions=(".xlsx",), generic_ok=True``."""
    if upload is None:
        raise ApiError(422, "validation_error", fields={field: [_("This file is required.")]})

    limit = settings.API_UPLOAD_MAX_BYTES if max_bytes is None else max_bytes
    if upload.size is not None and upload.size > limit:
        raise ApiError(413, "file_too_large", _("The file is too large. The maximum is %(max)s.") % {"max": filesizeformat(limit)})

    if upload.size == 0 and not allow_empty:
        raise ApiError(422, "validation_error", fields={field: [_("The file is empty.")]})

    allowed = settings.API_UPLOAD_CONTENT_TYPES if content_types is None else content_types
    content_type = (getattr(upload, "content_type", "") or "").split(";")[0].strip().lower()
    if allowed and content_type not in {t.lower() for t in allowed} and not (generic_ok and extensions and content_type in GENERIC_TYPES):
        raise ApiError(415, "unsupported_media_type", _("This file type isn't accepted."))

    if extensions:
        extension = os.path.splitext(upload.name or "")[1].lower()
        if extension not in {e.lower() for e in extensions}:
            raise ApiError(415, "unsupported_media_type", _("This file type isn't accepted."))

    return upload
