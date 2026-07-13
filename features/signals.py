"""Keep waffle's flag cache honest when club targeting changes.

waffle caches a flag's M2M ids and only flushes on ``save()``. Editing an M2M
does not call ``save()``, so adding or removing a club would otherwise leave a
stale cached set and the flag would keep answering with the old value.
"""

from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from .models import Flag

FLUSH_ACTIONS = {"post_add", "post_remove", "post_clear"}


@receiver(m2m_changed, sender=Flag.clubs.through)
def flush_flag_club_cache(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in FLUSH_ACTIONS:
        return

    if isinstance(instance, Flag):
        instance.flush()
    else:
        # Reverse edit (club.flags.add(flag)): flush each flag touched.
        for flag in Flag.objects.filter(pk__in=pk_set or []):
            flag.flush()
