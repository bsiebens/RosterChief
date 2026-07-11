import uuid

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils.translation import gettext_lazy as _

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(_("email"), unique=True, db_index=True)

    is_staff = models.BooleanField(_("is staff?"), default=False)
    is_active = models.BooleanField(_("is active?"), default=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["email"]

    def __str__(self):
        return self.get_full_name()

    def get_full_name(self):
        member = getattr(self, "member", None)
        if member is not None:
            return member.get_full_name()
        return self.email

    def get_short_name(self):
        member = getattr(self, "member", None)
        if member is not None:
            return member.get_short_name()
        return self.email
