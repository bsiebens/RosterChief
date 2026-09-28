"""Moves every existing Member.photo out of the default (public) storage into
private storage, under the same name, then deletes the public copy.

Idempotent: a photo already in private storage is left alone (its public copy
is removed if one is still there), and a row whose file is missing from both
places is skipped and left as it is. The source is whatever the default storage
is (local MEDIA_ROOT or the S3 bucket); the target is rosterchief.storage's
private storage.
"""

import logging

from django.core.files.storage import default_storage
from django.db import migrations

from rosterchief.storage import get_private_storage

logger = logging.getLogger(__name__)


def _move(names, source, target):
    for name in names:
        if target.exists(name):
            if source.exists(name):
                source.delete(name)
            continue
        if not source.exists(name):
            logger.warning("Member photo %s not found in either storage; left as is.", name)
            continue
        with source.open(name, "rb") as original:
            saved = target.save(name, original)
        if saved != name:
            # Never expected (target.exists(name) was false just above); keep the
            # row pointing at a file that exists rather than guess.
            target.delete(saved)
            logger.warning("Member photo %s could not be stored under its own name; left in place.", name)
            continue
        source.delete(name)


def _photo_names(apps):
    Member = apps.get_model("members", "Member")
    return list(Member.objects.exclude(photo="").exclude(photo__isnull=True).values_list("photo", flat=True).distinct())


def move_photos_to_private_storage(apps, schema_editor):
    _move(_photo_names(apps), default_storage, get_private_storage())


def move_photos_back_to_default_storage(apps, schema_editor):
    _move(_photo_names(apps), get_private_storage(), default_storage)


class Migration(migrations.Migration):
    dependencies = [
        ("members", "0011_member_photo_private_storage"),
    ]

    operations = [
        migrations.RunPython(move_photos_to_private_storage, move_photos_back_to_default_storage),
    ]
