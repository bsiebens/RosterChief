"""Product pictures and files leave storage when their row is deleted (a rollback never loses a file still referenced)."""

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from shop.models import ProductAttachment, ProductImage


def _remove(field_file) -> None:
    name, storage = field_file.name, field_file.storage
    if name:
        transaction.on_commit(lambda: storage.delete(name))


@receiver(post_delete, sender=ProductImage, dispatch_uid="shop.product_image_file")
def remove_image_file(sender, instance, **kwargs):
    _remove(instance.image)


@receiver(post_delete, sender=ProductAttachment, dispatch_uid="shop.product_attachment_file")
def remove_attachment_file(sender, instance, **kwargs):
    _remove(instance.file)
