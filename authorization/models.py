from django.contrib.auth.models import Group
from django.db import models
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel


class Role(UUIDModel):
    """A club-defined role. Lives in the club schema, backed by a Django Group."""

    group = models.OneToOneField(Group, verbose_name=_("group"), on_delete=models.CASCADE, related_name="role")
    slug = models.SlugField(_("slug"), unique=True)
    description = models.CharField(_("description"), max_length=255, blank=True)
    is_system = models.BooleanField(_("is system"), default=False, help_text=_("System roles cannot be deleted."))

    class Meta:
        verbose_name = _("role")
        verbose_name_plural = _("roles")

    def __str__(self):
        return self.group.name
