import uuid

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils.translation import gettext_lazy as _

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    class EmailNotifications(models.TextChoices):
        ALWAYS = "always", _("Always")
        UNLESS_APP = "unless_app", _("Not when the app can notify me")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(_("email"), unique=True, db_index=True)

    is_staff = models.BooleanField(_("is staff?"), default=False)
    is_active = models.BooleanField(_("is active?"), default=True)
    email_notifications = models.CharField(
        _("email notifications"),
        max_length=16,
        choices=EmailNotifications.choices,
        default=EmailNotifications.ALWAYS,
        help_text=_('Whether notifications are also emailed to this account. "Not when the app can notify me" skips the email while the account has the mobile app installed; mails with an attachment (such as an invoice) are always sent.'),
    )

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
