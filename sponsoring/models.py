from django.db import models
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel


class Sponsor(UUIDModel):
    name = models.CharField(_("name"), max_length=250)

    class Meta:
        verbose_name = _("sponsor")
        verbose_name_plural = _("sponsors")
