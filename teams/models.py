from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel


class NumberPool(UUIDModel):
    name = models.CharField(_("name"), max_length=250)
    min_number = models.PositiveSmallIntegerField(_("minimum number"))
    max_number = models.PositiveSmallIntegerField(_("maximum number"))
    min_age_gap_years = models.PositiveSmallIntegerField(_("minimum age gap (years)"), default=5, help_text=_("Players born at least this many years apart may share a number. 0 = never shared."))

    class Meta:
        verbose_name = _("number pool")
        verbose_name_plural = _("number pools")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.min_number is not None and self.max_number is not None and self.min_number > self.max_number:
            raise ValidationError({"max_number": _("Must be greater than or equal to the minimum number.")})
