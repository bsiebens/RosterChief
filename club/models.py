import uuid

from django.db import models
from django_tenants.models import DomainMixin
from tenant_users.tenants.models import TenantBase, UserProfile


class User(UserProfile):
    """Platform user (public schema). Email is the username."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    first_name = models.CharField(max_length=75)
    last_name = models.CharField(max_length=75)

    def __str__(self):
        return self.email


class Club(TenantBase):
    """A club is a tenant. TenantBase provides owner, slug, created and modified."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)

    auto_create_schema = True

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    pass


# Some other general models that will be used later on for different parts of the platform
