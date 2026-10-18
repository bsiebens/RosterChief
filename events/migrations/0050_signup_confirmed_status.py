from django.db import migrations, models


def mark_assigned_confirmed(apps, schema_editor):
    """Until now ACCEPTED meant "assigned" (accepting created the assignment); that is
    now CONFIRMED, and ACCEPTED means only "signed up"."""
    for signup_model, assignment_model, related in (("RefereeSignup", "EventReferee", "referees"), ("OfficialSignup", "EventOfficial", "officials")):
        Signup = apps.get_model("events", signup_model)
        Assignment = apps.get_model("events", assignment_model)
        for event_id, member_id in Assignment.objects.filter(member__isnull=False).values_list("event_id", "member_id"):
            Signup.objects.filter(event_id=event_id, member_id=member_id).update(status="confirmed")


class Migration(migrations.Migration):
    dependencies = [("events", "0049_sport")]

    operations = [
        migrations.AlterField(
            model_name="refereesignup",
            name="status",
            field=models.CharField(choices=[("invited", "invited"), ("accepted", "signed up"), ("confirmed", "confirmed"), ("declined", "declined")], default="invited", max_length=20, verbose_name="status"),
        ),
        migrations.AlterField(
            model_name="officialsignup",
            name="status",
            field=models.CharField(choices=[("invited", "invited"), ("accepted", "signed up"), ("confirmed", "confirmed"), ("declined", "declined")], default="invited", max_length=20, verbose_name="status"),
        ),
        migrations.RunPython(mark_assigned_confirmed, migrations.RunPython.noop),
    ]
