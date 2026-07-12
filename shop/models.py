from django.db import models
from django.db.models import UniqueConstraint
from django.utils.translation import gettext_lazy as _

from clubmanager.base import ClubScopedModel


class Product(ClubScopedModel):
    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=255, blank=True)

    slug_source = "name"

    class Meta:
        constraints = [
            UniqueConstraint(fields=["club", "slug"], name="unique_product_slug_per_club"),
        ]
