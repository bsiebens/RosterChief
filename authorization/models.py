from django.contrib.auth.models import Group
from django.db import models

from rosterchief.base import UUIDModel


class Role(UUIDModel):
    """A club-defined role. Lives in the club schema, backed by a Django Group."""

    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name="role")
    slug = models.SlugField(unique=True)
    description = models.CharField(max_length=255, blank=True)
    is_system = models.BooleanField(default=False, help_text="System roles cannot be deleted.")

    def __str__(self):
        return self.group.name
