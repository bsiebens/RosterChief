"""Validators shared by the club app's upload fields."""

from django.core.validators import FileExtensionValidator
from django.utils.deconstruct import deconstructible

#: Raster formats only. Uploaded logos are served from the same origin as the
#: application, and SVG is an active document format (it can carry script), so
#: it is not accepted for new uploads.
LOGO_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp"]

#: The matching ``accept=`` value for logo file inputs.
LOGO_ACCEPT = "image/png,image/jpeg,image/gif,image/webp"


@deconstructible
class NewUploadExtensionValidator(FileExtensionValidator):
    """A FileExtensionValidator that only checks newly assigned files.

    A file already committed to storage is left alone, so a row that predates a
    tightened extension list (e.g. an existing SVG logo) keeps saving through
    unrelated edits; the restriction applies as soon as a new file is uploaded.
    """

    def __call__(self, value):
        if getattr(value, "_committed", False):
            return
        super().__call__(value)
