import uuid

from django.db import models


class UUIDModel(models.Model):
    """Abstract base class giving every model a UUID primary key"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class ClubScopedModel(UUIDModel):
    """Abstract base for entities owned by a single club (tenant root)."""

    club = models.ForeignKey("club.Club", on_delete=models.CASCADE, related_name="%(class)ss")

    class Meta:
        abstract = True
