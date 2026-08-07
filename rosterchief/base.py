import uuid
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from club.tenancy import require_current_club

if TYPE_CHECKING:
    from club.models import Club


def validate_club_scope(instance, owning_club_id, *, same_club_fields=(), member_fields=()):
    """Reject FKs that leak across clubs.

    ``same_club_fields`` are FKs to club-scoped models that must share
    ``owning_club_id``; ``member_fields`` are Member FKs whose target must have
    a ClubMembership in that club. Unset (None) FKs are skipped. Call from a
    model's ``clean()``.
    """
    errors = {}
    for field in same_club_fields:
        if getattr(instance, f"{field}_id") is not None and getattr(instance, field).club_id != owning_club_id:
            errors[field] = _("Must belong to the same club.")

    if member_fields:
        from club.models import ClubMembership

        for field in member_fields:
            if getattr(instance, f"{field}_id") is not None and not ClubMembership.objects.filter(club_id=owning_club_id, member=getattr(instance, field)).exists():
                errors[field] = _("Must be a member of this club.")

    if errors:
        raise ValidationError(errors)


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


class TimeStampedModel(models.Model):
    """Row birthdays. Without these, every metric can only describe the present —
    "42 members" is knowable, "members joined this month" is not."""

    created = models.DateTimeField(_("created"), auto_now_add=True)
    modified = models.DateTimeField(_("modified"), auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(TimeStampedModel):
    """Abstract base class giving every model a UUID primary key and timestamps."""

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
