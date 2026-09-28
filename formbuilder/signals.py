"""Keep a FILE answer's stored upload in step with its row: deleting an Answer
-- directly, or through its Submission/FormSend/Form cascade -- also deletes
the file from private storage, so personal documents don't outlive the
response they belonged to.

Connecting a post_delete receiver also stops Django's fast-delete
optimisation for those cascades, so this runs for every Answer instance.
"""

from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Answer


@receiver(post_delete, sender=Answer)
def delete_answer_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)
