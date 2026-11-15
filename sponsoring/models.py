from django.core.exceptions import ValidationError
from django.core.files.images import get_image_dimensions
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel


def sponsor_logo_path(instance, filename):
    return f"sponsors/{instance.pk}/{filename}"


class Sponsor(UUIDModel):
    name = models.CharField(_("name"), max_length=255)
    logo = models.FileField(
        _("logo"),
        upload_to=sponsor_logo_path,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=["png", "jpg", "jpeg", "webp"])],
    )
    logo_width = models.PositiveIntegerField(_("logo width"), null=True, blank=True, editable=False)
    logo_height = models.PositiveIntegerField(_("logo height"), null=True, blank=True, editable=False)
    url = models.URLField(_("URL"), blank=True)
    start_date = models.DateField(_("start date"), default=timezone.localdate)
    end_date = models.DateField(_("end date"), null=True, blank=True)

    class Meta:
        verbose_name = _("sponsor")
        verbose_name_plural = _("sponsors")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": _("End date can't be before the start date.")})

    def save(self, *args, **kwargs):
        if not self.logo:
            self.logo_width, self.logo_height = None, None
        elif not self.logo._committed:
            self.logo_width, self.logo_height = get_image_dimensions(self.logo)
        update_fields = kwargs.get("update_fields")
        if update_fields and "logo" in update_fields:
            kwargs["update_fields"] = {*update_fields, "logo_width", "logo_height"}
        super().save(*args, **kwargs)

    @classmethod
    def active(cls, today=None):
        today = today or timezone.localdate()
        return cls.objects.filter(start_date__lte=today).filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
