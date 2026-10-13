import django.db.models.deletion  # noqa: F401
from django.db import migrations, models

import members.models
import rosterchief.storage


class Migration(migrations.Migration):
    dependencies = [
        ("members", "0012_move_member_photos_to_private_storage"),
    ]

    operations = [
        migrations.RenameField(model_name="member", old_name="photo", new_name="player_photo"),
        migrations.AlterField(
            model_name="member",
            name="player_photo",
            field=members.models.MemberPhotoField(blank=True, storage=rosterchief.storage.get_private_storage, upload_to=members.models.member_photo_path, verbose_name="player picture"),
        ),
        migrations.AddField(
            model_name="member",
            name="profile_photo",
            field=members.models.MemberPhotoField(blank=True, storage=rosterchief.storage.get_private_storage, upload_to=members.models.member_photo_path, verbose_name="profile picture"),
        ),
        migrations.AlterField(
            model_name="member",
            name="photo_public_consent",
            field=models.BooleanField(default=False, verbose_name="show the player picture on the public website/API"),
        ),
    ]
