from django.db import models
from django_tenants.models import DomainMixin, TenantMixin

from rosterchief.base import UUIDModel


class Sponsor(UUIDModel):
    name = models.CharField(max_length=250)
