import uuid
from typing import TYPE_CHECKING

from django.db import models

from club.tenancy import require_current_club

if TYPE_CHECKING:
    from club.models import Club


class TenantQuerySet(models.QuerySet):
    def for_club(self, club: Club):
        return self.filter(club=club)

    def current_club(self):
        return self.filter(club=require_current_club())


class UUIDModel(models.Model):
    """Abstract base class giving every model a UUID primary key"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class ClubScopedModel(UUIDModel):
    """Abstract base for entities owned by a single club (tenant root)."""

    club = models.ForeignKey("club.Club", on_delete=models.CASCADE, related_name="%(class)ss")
    objects = TenantQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.club_id is None:
            self.club = require_current_club()

        super().save(*args, **kwargs)
