import uuid
from typing import TYPE_CHECKING

from django.db import models
from django.utils.text import slugify

from club.tenancy import require_current_club

if TYPE_CHECKING:
    from club.models import Club


def unique_slugify(instance, value, *, slug_field="slug", scope=None):
    """Return a slug derived from ``value``, unique within ``scope``.

    Truncates to the slug field's ``max_length`` and appends ``-2``, ``-3``, …
    on collision. ``scope`` is a dict of field lookups the uniqueness is
    checked within (e.g. ``{"club": club}`` for per-club, ``{}``/``None`` for
    global).
    """
    max_length = instance._meta.get_field(slug_field).max_length
    base = slugify(value)[:max_length] or "item"

    queryset = type(instance)._default_manager.exclude(pk=instance.pk)
    if scope:
        queryset = queryset.filter(**scope)

    slug = base
    suffix = 2
    while queryset.filter(**{slug_field: slug}).exists():
        tail = f"-{suffix}"
        slug = f"{base[: max_length - len(tail)]}{tail}"
        suffix += 1
    return slug


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

    # Subclasses with a ``slug`` field set this to the source field name (e.g.
    # "name"/"title") to auto-populate the slug — unique per club — on save.
    slug_source = None

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.club_id is None:
            self.club = require_current_club()

        if self.slug_source and not self.slug:
            self.slug = unique_slugify(self, getattr(self, self.slug_source), scope={"club": self.club})

        super().save(*args, **kwargs)
