from django.db import migrations

# The evaluation permissions used to live under ``members`` ("execute" and "create"). Roles that hold them keep them under the new
# codes: members.execute_evaluations -> evaluations.execute_evaluations and members.create_evaluations -> evaluations.manage_evaluations.
# The new rows may already exist (the permission catalog is synced on its own), so the roles are re-pointed rather than the rows renamed.
MOVES = {"execute_evaluations": "execute_evaluations", "create_evaluations": "manage_evaluations"}


def move(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    old = ContentType.objects.filter(app_label="members", model="clubpermission").first()
    if old is None:
        return
    new, _ = ContentType.objects.get_or_create(app_label="evaluations", model="clubpermission")
    for old_code, new_code in MOVES.items():
        old_permission = Permission.objects.filter(content_type=old, codename=old_code).first()
        if old_permission is None:
            continue
        new_permission, _ = Permission.objects.get_or_create(content_type=new, codename=new_code, defaults={"name": old_permission.name})
        for group in Group.objects.filter(permissions=old_permission):
            group.permissions.add(new_permission)
        old_permission.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("evaluations", "0001_initial"),
        ("auth", "0001_initial"),
        ("contenttypes", "0001_initial"),
    ]
    operations = [migrations.RunPython(move, migrations.RunPython.noop)]
