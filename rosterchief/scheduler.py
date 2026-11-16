"""Beat scheduler entry point that works in a process where Django is not set up yet.

``celery worker -B`` starts beat in a fresh (spawned) process on macOS; that process imports the scheduler class before
anything has called ``django.setup()``, and django-celery-beat's models then fail with "Apps aren't loaded yet".
Importing the scheduler through this module sets Django up first. It is a no-op when Django is already set up.

This module also provides the seed-only variant used by the project.
"""

import django

django.setup()

from django_celery_beat.schedulers import DatabaseScheduler  # noqa: E402


class SeedOnlyDatabaseScheduler(DatabaseScheduler):
    """django-celery-beat's scheduler, except code-defined entries are only *created* when missing.

    The stock scheduler rewrites every ``beat_schedule`` entry's schedule on each start, so editing a time in the
    database is silently undone by the next restart. Here the database wins once a row exists.
    """

    def update_from_dict(self, mapping):
        existing = set(self.Model.objects.values_list("name", flat=True))
        super().update_from_dict({name: entry for name, entry in mapping.items() if name not in existing})


__all__ = ["DatabaseScheduler", "SeedOnlyDatabaseScheduler"]
