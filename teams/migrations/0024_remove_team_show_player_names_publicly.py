from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("teams", "0023_team_tournament_only_and_ordering"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="team",
            name="show_player_names_publicly",
        ),
    ]
