import re

from django.db import migrations

#: Matches a "U<number>" youth age-group marker in a team's name/short name
#: (e.g. "U14", "u16 boys") -- the same pattern mobile.coach_views.CoachAdd
#: PlayerView used to parse live on every request before Team carried a real
#: age field. One-time use here to recover what that regex could ever have
#: produced; a team that doesn't match stays unset, same as before.
AGE_GROUP_RE = re.compile(r"u(\d+)", re.IGNORECASE)


def backfill_age_from_team_name(apps, schema_editor):
    Team = apps.get_model("teams", "Team")
    for team in Team.objects.filter(age_min__isnull=True, age_max__isnull=True):
        match = AGE_GROUP_RE.search(team.name) or AGE_GROUP_RE.search(team.short_name)
        if match is None:
            continue
        age = int(match.group(1))
        team.age_min = age
        team.age_max = age
        team.save(update_fields=["age_min", "age_max"])


class Migration(migrations.Migration):
    dependencies = [
        ("teams", "0015_team_age_max_team_age_min_team_feeds_into"),
    ]

    operations = [
        migrations.RunPython(backfill_age_from_team_name, migrations.RunPython.noop),
    ]
