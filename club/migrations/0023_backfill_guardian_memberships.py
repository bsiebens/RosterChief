"""Reclassify existing family parents as guardians.

Before ``kind`` existed, members/services/family.py enrolled a parent exactly like
the child they were registering, so every parent already in the database holds a
full membership and is counted as a member.

The guard matters more than the rule: anyone who is *also* on a roster, on a
team's staff, or holds an elevated ClubRole is left as a member. A parent who
plays, coaches or runs the club is a member who happens to have children there,
and silently demoting them would strip them out of the member list and their own
team's roster eligibility. Anything ambiguous stays as it is -- an admin can flip
a membership to guardian by hand, which is cheap; noticing that someone quietly
vanished is not.
"""

from django.db import migrations


def backfill_guardians(apps, schema_editor):
    ClubMembership = apps.get_model("club", "ClubMembership")
    FamilyMembership = apps.get_model("members", "FamilyMembership")
    TeamMembership = apps.get_model("teams", "TeamMembership")
    StaffAssignment = apps.get_model("teams", "StaffAssignment")
    ClubRole = apps.get_model("club", "ClubRole")

    parent_ids = set(FamilyMembership.objects.filter(role__in=["parent", "guardian"]).values_list("member_id", flat=True))
    if not parent_ids:
        return

    for membership in ClubMembership.objects.filter(member_id__in=parent_ids).iterator():
        club_id, member_id = membership.club_id, membership.member_id

        plays = TeamMembership.objects.filter(member_id=member_id, team__club_id=club_id).exists()
        on_staff = StaffAssignment.objects.filter(member_id=member_id, team__club_id=club_id).exists()
        runs_the_club = ClubRole.objects.filter(member_id=member_id, club_id=club_id, role__in=["admin", "editor"]).exists()
        if plays or on_staff or runs_the_club:
            continue

        membership.kind = "guardian"
        # Guardians owe nothing; clear any fee the old parent-as-member flow left behind.
        membership.fee_amount = 0
        membership.save(update_fields=["kind", "fee_amount"])


def restore_members(apps, schema_editor):
    """Everything was a member before this migration ran."""
    ClubMembership = apps.get_model("club", "ClubMembership")
    ClubMembership.objects.filter(kind="guardian").update(kind="member")


class Migration(migrations.Migration):
    dependencies = [
        ("club", "0022_clubmembership_kind"),
        ("members", "0004_group_groupmembership_and_more"),
        ("teams", "0009_team_referee_management"),
    ]

    operations = [migrations.RunPython(backfill_guardians, restore_members)]
