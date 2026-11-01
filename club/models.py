import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _
from django_extensions.db.fields import AutoSlugField
from django_tenants.models import DomainMixin
from tenant_users.tenants.models import TenantBase, UserProfile

from rosterchief.base import UUIDModel


class User(UserProfile):
    """Platform user (public schema). Email is the username."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    first_name = models.CharField(max_length=75)
    last_name = models.CharField(max_length=75)

    def __str__(self) -> str:
        return self.email

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["last_name", "first_name"]


class Club(TenantBase):
    """A club is a tenant. TenantBase provides owner, slug, created and modified."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    slug = AutoSlugField(_("slug"), unique=True, populate_from=["name"])

    sport = models.ForeignKey("club.Sport", null=True, on_delete=models.PROTECT, related_name="clubs")

    auto_create_schema = True

    def __str__(self) -> str:
        return self.name

    class Meta:
        verbose_name = _("club")
        verbose_name_plural = _("clubs")
        ordering = ["name"]


class Domain(DomainMixin):
    pass


# Some other general models that will be used later on for different parts of the platform
class Sport(UUIDModel):
    """A club can be marked as belonging to a given sport. This would enable further things like specific competitions etc."""

    name = models.CharField(_("name"), max_length=250)
    slug = AutoSlugField(_("slug"), unique=True, populate_from=["name"])

    def __str__(self) -> str:
        return self.name

    class Meta:
        verbose_name = _("sport")
        verbose_name_plural = _("sports")
        ordering = ["name"]
