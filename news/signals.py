"""Keep NewsPhoto's file in sync with the row: deleting a NewsPhoto -- one at
a time, or in bulk via a News item's cascade -- must also delete the image
from storage, or it just accumulates orphaned files forever.

Connecting a post_delete receiver also stops Django's fast-delete
optimisation for a News cascade, so every NewsPhoto instance (and this
signal) actually runs instead of being collapsed into one bulk SQL DELETE.
"""

from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import NewsPhoto


@receiver(post_delete, sender=NewsPhoto)
def delete_photo_file(sender, instance, **kwargs):
    instance.image.delete(save=False)
