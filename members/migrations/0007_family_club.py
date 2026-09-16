import django.db.models.deletion
from django.db import migrations, models


def backfill_family_club(apps, schema_editor):
    """Assign each existing Family a club, derived from its own members'
    ClubMembership rows -- a family predates this column, so there's no
    other signal to go on. Unambiguous for the overwhelming case (every
    member of the family belongs to the same one club); an ambiguous or
    club-less family (leftover test/debug data with no real ClubMembership
    at all) falls back to the platform's oldest club rather than blocking
    the migration -- there is no correct answer for data that was never
    club-scoped to begin with."""
    Family = apps.get_model("members", "Family")
    ClubMembership = apps.get_model("club", "ClubMembership")
    Club = apps.get_model("club", "Club")

    fallback_club_id = Club.objects.order_by("created").values_list("pk", flat=True).first()

    for family in Family.objects.all():
        member_ids = list(family.memberships.values_list("member_id", flat=True))
        club_ids = list(ClubMembership.objects.filter(member_id__in=member_ids).values_list("club_id", flat=True).distinct())

        if len(club_ids) == 1:
            family.club_id = club_ids[0]
        elif club_ids:
            # More than one distinct club among this family's members --
            # shouldn't happen for real data (a household registers with one
            # club), but pick whichever club most of its members actually
            # belong to rather than fail the migration outright.
            counts = {}
            for club_id in ClubMembership.objects.filter(member_id__in=member_ids).values_list("club_id", flat=True):
                counts[club_id] = counts.get(club_id, 0) + 1
            family.club_id = max(counts, key=counts.get)
        elif fallback_club_id is not None:
            family.club_id = fallback_club_id
        else:
            continue

        family.save(update_fields=["club"])


class Migration(migrations.Migration):

    dependencies = [
        ("club", "0038_clubmembership_club_clubme_club_id_646d5d_idx_and_more"),
        ("members", "0006_parentclaim_submitted_by_user"),
    ]

    operations = [
        migrations.AddField(
            model_name="family",
            name="club",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="%(class)ss", to="club.club"),
        ),
        migrations.RunPython(backfill_family_club, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="family",
            name="club",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(class)ss", to="club.club"),
        ),
    ]
