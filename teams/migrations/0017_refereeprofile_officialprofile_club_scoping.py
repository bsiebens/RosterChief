import django.db.models.deletion
from django.db import migrations, models


def backfill_profile_club(apps, model_name):
    """Shared backfill for RefereeProfile/OfficialProfile: the level (already
    club-scoped) tells us the club whenever one is set; a profile with no
    level yet falls back to the member's own (single, in practice) club
    membership, and finally to the platform's oldest club for leftover
    test/debug rows with no ClubMembership at all -- same last-resort
    reasoning as members.migrations.0007_family_club."""
    Profile = apps.get_model("teams", model_name)
    ClubMembership = apps.get_model("club", "ClubMembership")
    Club = apps.get_model("club", "Club")

    fallback_club_id = Club.objects.order_by("created").values_list("pk", flat=True).first()

    for profile in Profile.objects.select_related("level"):
        if profile.level_id is not None:
            profile.club_id = profile.level.club_id
        else:
            club_ids = list(ClubMembership.objects.filter(member_id=profile.member_id).values_list("club_id", flat=True).distinct())
            if club_ids:
                profile.club_id = club_ids[0]
            elif fallback_club_id is not None:
                profile.club_id = fallback_club_id
            else:
                continue
        profile.save(update_fields=["club"])


def backfill_referee_profile_club(apps, schema_editor):
    backfill_profile_club(apps, "RefereeProfile")


def backfill_official_profile_club(apps, schema_editor):
    backfill_profile_club(apps, "OfficialProfile")


class Migration(migrations.Migration):

    dependencies = [
        ("club", "0038_clubmembership_club_clubme_club_id_646d5d_idx_and_more"),
        ("teams", "0016_backfill_age_from_team_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="refereeprofile",
            name="club",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="%(class)ss", to="club.club"),
        ),
        migrations.AddField(
            model_name="officialprofile",
            name="club",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="%(class)ss", to="club.club"),
        ),
        migrations.RunPython(backfill_referee_profile_club, migrations.RunPython.noop),
        migrations.RunPython(backfill_official_profile_club, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="refereeprofile",
            name="club",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(class)ss", to="club.club"),
        ),
        migrations.AlterField(
            model_name="officialprofile",
            name="club",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(class)ss", to="club.club"),
        ),
        migrations.AlterField(
            model_name="refereeprofile",
            name="member",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="referee_profiles", to="members.member", verbose_name="member"),
        ),
        migrations.AlterField(
            model_name="officialprofile",
            name="member",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="official_profiles", to="members.member", verbose_name="member"),
        ),
        migrations.AddConstraint(
            model_name="refereeprofile",
            constraint=models.UniqueConstraint(fields=("club", "member"), name="unique_referee_profile_per_club_per_member"),
        ),
        migrations.AddConstraint(
            model_name="officialprofile",
            constraint=models.UniqueConstraint(fields=("club", "member"), name="unique_official_profile_per_club_per_member"),
        ),
    ]
