from django.contrib.auth.base_user import AbstractBaseUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from clubmanager.base import ClubScopedModel, UUIDModel


class Club(UUIDModel):
    name = models.CharField(max_length=255)

    class Meta:
        verbose_name = _("club")
        verbose_name_plural = _("clubs")
        ordering = ["name"]

    def __str__(self):
        return self.name


class ClubMembership(UUIDModel): ...
