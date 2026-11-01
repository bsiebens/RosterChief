# import uuid
# from typing import TYPE_CHECKING
#
# from django.core.exceptions import ValidationError
# from django.db import models
# from django.utils.text import slugify
# from django.utils.translation import gettext_lazy as _
#
# from club.tenants import require_current_club
#
# if TYPE_CHECKING:
#     from club.models import Club
#
#
# def unique_slugify(instance, value, *, slug_field="slug", scope: dict | None = None) -> str:
#     """Return a slug derived from ``value``, unique within the provided scope.
#
#     Truncates the slug field's ``max_length``and appends ``-2``, ``-3``, ...
#     on collision. ``scope`` is a dict of field lookups the uniqueness is checked within
#     (e.g. ``{"club": club}`` for per-club, ``{}``or ``None``for global."""
#
#     max_length = instance._meta.get_field(slug_field).max_length
#     base = slugify(value)[:max_length] or "item"
#
#     queryset = type(instance)._default_manager.exclude(pk=instance.pk)
#     if scope:
#         queryset = queryset.filter(**scope)
#
#     slug = base
#     suffix = 2
#     while queryset.filter(**{slug_field: slug}).exists():
#         tail = f"-{suffix}"
#         slug = f"{base[: max_length - len(tail)]}{tail}"
#         suffix += 1
#
#     return slug
#
#
# class ClubQuerySet(models.QuerySet):
#     def for_club(self, club: Club):
#         return self.filter(club=club)
#
#     def current_club(self):
#         return self.filter(club=require_current_club())
#
#
# class TimeStampedModel(models.Model):
#     """Keep auto track of created and modified timestamps"""
#
#     created = models.DateTimeField(_("created"), auto_now_add=True)
#     modified = models.DateTimeField(_("modified"), auto_now=True)
#
#     class Meta:
#         abstract = True
#
#
# class UUIDModel(TimeStampedModel):
#     """Use UUIDs as primary key"""
#
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#
#     class Meta:
#         abstract = True
#
#
# class ClubScopedModel(UUIDModel):
#     """A basic model for entities owned by a given club."""
#
#     club = models.ForeignKey("club.Club", on_delete=models.CASCADE, related_name="%(class)ss")
#     objects = ClubQuerySet._as_manager()
#
#     slug_source = None
#
#     class Meta:
#         abstract = True
#
#     def save(self, *args, **kwargs):
#         if self.club_id is None:
#             self.club = require_current_club()
#
#         if self.slug_source and not self.slug:
#             self.slug = unique_slugify(self, getattr(self, self.slug_source), scope={"club": self.club})
#
#         super().save(*args, **kwargs)
import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _


class UUIDModel(models.Model):
    """A basic model using UUIDs as primary key and timestamped objects."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    created = models.DateTimeField(_("created"), auto_now_add=True)
    modified = models.DateTimeField(_("modified"), auto_now=True)

    class Meta:
        abstract = True
