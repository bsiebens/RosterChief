"""Size and type limits for documents uploaded through public or member-facing
forms (the registration status page, formbuilder FILE fields)."""

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.template.defaultfilters import filesizeformat
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _

MAX_DOCUMENT_UPLOAD_BYTES = 10 * 1024 * 1024

#: PDFs plus the image formats phones and scanners produce.
DOCUMENT_EXTENSIONS = ["pdf", "jpg", "jpeg", "png", "webp", "heic", "heif"]

#: The matching ``accept=`` value for document file inputs.
DOCUMENT_ACCEPT = ".pdf,.jpg,.jpeg,.png,.webp,.heic,.heif,application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif"


@deconstructible
class MaxFileSizeValidator:
    message = _("This file is too large (%(size)s). The maximum is %(max_size)s.")
    code = "file_too_large"

    def __init__(self, max_bytes):
        self.max_bytes = max_bytes

    def __call__(self, value):
        size = getattr(value, "size", None)
        if size is not None and size > self.max_bytes:
            raise ValidationError(self.message, code=self.code, params={"size": filesizeformat(size), "max_size": filesizeformat(self.max_bytes)})

    def __eq__(self, other):
        return isinstance(other, MaxFileSizeValidator) and self.max_bytes == other.max_bytes


def document_upload_validators():
    return [
        MaxFileSizeValidator(MAX_DOCUMENT_UPLOAD_BYTES),
        FileExtensionValidator(DOCUMENT_EXTENSIONS, message=_("“%(extension)s” files are not accepted. Allowed file types: %(allowed_extensions)s.")),
    ]
