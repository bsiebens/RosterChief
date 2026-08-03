import datetime
import sys
from decimal import Decimal
from io import BytesIO
from unittest import mock

import openpyxl
from allauth.mfa.models import Authenticator
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from club.models import Club, ClubMembership, ClubRole, FeePayment, Season
from events.models import Event
from management.bulk_import import TEMPLATE_COLUMNS
from management.pdf import PDFExportError, render_pdf
from members.models import Family, FamilyMembership, Member
from news.models import News, NewsPhoto
from shop.models import Order
from teams.models import Position, StaffAssignment, Team, TeamMembership

User = get_user_model()

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def make_import_workbook(rows):
    """An in-memory .xlsx upload -- header matching the real template, plus
    whatever data rows a test wants to exercise."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(TEMPLATE_COLUMNS)
    for row in rows:
        sheet.append(row)

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return SimpleUploadedFile("members.xlsx", buffer.read(), content_type=XLSX_CONTENT_TYPE)


def enrol_mfa(user):
    return Authenticator.objects.create(user=user, type=Authenticator.Type.TOTP, data={"secret": "JBSWY3DPEHPK3PXP"})


def make_season(club):
    # Must genuinely cover *today*: current_season()/teams_staffed_by() key off
    # Season.covering(club, timezone.localdate()), not just any season row.
    today = timezone.localdate()
    return Season.objects.create(club=club, start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))


@override_settings(
    ROSTERCHIEF_BASE_DOMAIN="rosterchief.app",
    ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "rival-fc.rosterchief.app", "testserver"],
)
class ManagementTestBase(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.season = make_season(self.club)

        self.admin_user = User.objects.create_user(email="admin@example.com", password="pw-secret-123")
        self.admin_member = Member.objects.create(user=self.admin_user, first_name="Ada", last_name="Admin")
        ClubMembership.objects.create(club=self.club, member=self.admin_member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        ClubRole.objects.filter(club=self.club, member=self.admin_member).update(role=ClubRole.Roles.ADMIN)
        enrol_mfa(self.admin_user)

    def club_get(self, name, *args):
        return self.client.get(reverse(f"management:{name}", args=args), HTTP_HOST="ajax-united.rosterchief.app")

    def club_post(self, name, data, *args):
        return self.client.post(reverse(f"management:{name}", args=args), data, HTTP_HOST="ajax-united.rosterchief.app")


class AccessTests(ManagementTestBase):
    def test_anonymous_is_sent_to_login(self):
        response = self.club_get("home")

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response.url)

    def test_a_club_member_with_no_role_or_staff_assignment_gets_403(self):
        plain_user = User.objects.create_user(email="plain@example.com", password="pw-secret-123")
        self.client.force_login(plain_user)

        self.assertEqual(self.club_get("home").status_code, 403)

    def test_a_plain_active_club_member_gets_403(self):
        # An active ClubMembership auto-grants a MEMBER ClubRole (club/signals.py) --
        # every signed-up player has one. That alone must not be enough to get in, or
        # players would reach the staff UI they're explicitly excluded from.
        player_user = User.objects.create_user(email="player@example.com", password="pw-secret-123")
        player_member = Member.objects.create(user=player_user, first_name="Paul", last_name="Player")
        ClubMembership.objects.create(club=self.club, member=player_member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        self.client.force_login(player_user)

        self.assertEqual(self.club_get("home").status_code, 403)

    def test_a_club_admin_can_reach_it(self):
        self.client.force_login(self.admin_user)

        self.assertEqual(self.club_get("home").status_code, 200)

    def test_it_does_not_exist_on_the_base_domain(self):
        # The management UI manages one club; the mirror image of controlpanel
        # refusing to exist on a club subdomain.
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("management:home"), HTTP_HOST="rosterchief.app")

        self.assertEqual(response.status_code, 404)

    def test_staff_with_only_a_staff_assignment_can_reach_staff_pages(self):
        # No ClubRole at all -- authority comes purely from a current-season
        # StaffAssignment, per club.services.access.
        coach_user = User.objects.create_user(email="coach@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        position = Position.objects.create(club=self.club, name="Coach", short_name="C", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=position)
        self.client.force_login(coach_user)

        self.assertEqual(self.club_get("member_list").status_code, 200)

    def test_staff_without_admin_role_cannot_reach_admin_only_pages(self):
        coach_user = User.objects.create_user(email="coach2@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cody", last_name="Coach")
        team = Team.objects.create(club=self.club, name="U13", short_name="U13")
        position = Position.objects.create(club=self.club, name="Coach2", short_name="C2", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=position)
        self.client.force_login(coach_user)

        self.assertEqual(self.club_get("position_list").status_code, 403)
        self.assertEqual(self.club_post("member_create", {"first_name": "X", "last_name": "Y"}).status_code, 403)


class NavLinkTests(ManagementTestBase):
    """The global navbar's "Management" link, next to Django admin -- see
    management.context_processors.management_link and templates/_base.html."""

    def test_a_club_admin_sees_the_link_on_the_club_subdomain(self):
        self.client.force_login(self.admin_user)

        self.assertContains(self.club_get("home"), reverse("management:home"))

    def test_a_plain_active_club_member_does_not_see_the_link(self):
        player_user = User.objects.create_user(email="player2@example.com", password="pw-secret-123")
        player_member = Member.objects.create(user=player_user, first_name="Pia", last_name="Player")
        ClubMembership.objects.create(club=self.club, member=player_member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        self.client.login(email="player2@example.com", password="pw-secret-123")

        # 403 on /manage/ itself, but the link must not appear on pages this user *can*
        # reach either -- assert against a page outside the gate: the account view.
        response = self.client.get(reverse("mfa_index"), HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotContains(response, reverse("management:home"))

    def test_the_link_is_absent_on_the_base_domain_even_for_a_club_admin(self):
        # has_management_access requires a resolved club; the control panel/base domain
        # has none, so the link -- which points at a single club's management app --
        # correctly never appears there regardless of who's signed in.
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("mfa_index"), HTTP_HOST="rosterchief.app")

        self.assertNotContains(response, reverse("management:home"))


class ActiveNavHighlightTests(ManagementTestBase):
    """The sidebar/mobile nav highlights whichever section the current page
    belongs to -- see management.context_processors.active_nav_section."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)

    def test_the_members_list_page_highlights_members(self):
        response = self.club_get("member_list")

        self.assertContains(response, f'class="menu-active" href="{reverse("management:member_list")}"')
        self.assertNotContains(response, f'class="menu-active" href="{reverse("management:home")}"')

    def test_a_member_detail_sub_page_still_highlights_members(self):
        # member_detail has no nav entry of its own -- it belongs to the Members
        # section, same as member_list, member_update, family_detail, etc.
        member = Member.objects.create(first_name="Sub", last_name="Page")
        ClubMembership.objects.create(club=self.club, member=member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

        response = self.club_get("member_detail", member.pk)

        self.assertContains(response, f'class="menu-active" href="{reverse("management:member_list")}"')

    def test_the_dashboard_highlights_dashboard_only(self):
        response = self.club_get("home")

        self.assertContains(response, f'class="menu-active" href="{reverse("management:home")}"')
        self.assertNotContains(response, f'class="menu-active" href="{reverse("management:member_list")}"')


class MemberManagementTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)

    def test_member_list_is_scoped_to_the_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        other_season = make_season(other_club)
        other_member = Member.objects.create(first_name="Other", last_name="Person")
        ClubMembership.objects.create(club=other_club, member=other_member, season=other_season, status=ClubMembership.StatusChoices.ACTIVE)

        response = self.club_get("member_list")

        self.assertNotContains(response, "Other Person")

    def test_creating_a_member_also_signs_them_up_for_the_current_season(self):
        response = self.club_post("member_create", {"first_name": "New", "last_name": "Player", "email": "new@example.com"})

        member = Member.objects.get(first_name="New", last_name="Player")
        self.assertRedirects(response, reverse("management:member_detail", args=[member.pk]))
        self.assertTrue(ClubMembership.objects.filter(club=self.club, member=member, season=self.season).exists())

    def test_updating_a_member(self):
        member = Member.objects.create(first_name="Old", last_name="Name")
        ClubMembership.objects.create(club=self.club, member=member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

        # This member has a current-season membership, so its section renders too --
        # one combined submit, so its (required) fields must come along.
        self.club_post(
            "member_update",
            {"first_name": "New", "last_name": "Name", "status": ClubMembership.StatusChoices.ACTIVE, "fee_status": ClubMembership.FeeStatus.UNPAID},
            member.pk,
        )

        member.refresh_from_db()
        self.assertEqual(member.first_name, "New")


class TeamManagementTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)

    def test_team_list_is_scoped_to_the_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        Team.objects.create(club=other_club, name="Rival Team", short_name="RT")

        response = self.club_get("team_list")

        self.assertNotContains(response, "Rival Team")

    def test_creating_a_team(self):
        response = self.club_post("team_create", {"name": "U15", "short_name": "U15"})

        team = Team.objects.get(club=self.club, name="U15")
        self.assertRedirects(response, reverse("management:team_detail", args=[team.pk]))

    def test_deleting_a_team(self):
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")

        response = self.club_post("team_delete", {}, team.pk)

        self.assertRedirects(response, reverse("management:team_list"))
        self.assertFalse(Team.objects.filter(pk=team.pk).exists())

    def test_deleting_a_team_cascades_its_roster_and_staff(self):
        team = Team.objects.create(club=self.club, name="U17", short_name="U17")
        position = Position.objects.create(club=self.club, name="Coach17", short_name="C17", staff_position=True)
        member = Member.objects.create(first_name="Sam", last_name="Staffer")
        StaffAssignment.objects.create(team=team, member=member, season=self.season, position=position)

        self.club_post("team_delete", {}, team.pk)

        self.assertFalse(StaffAssignment.objects.filter(team=team).exists())

    def test_a_non_admin_cannot_delete_a_team(self):
        team = Team.objects.create(club=self.club, name="U18", short_name="U18")
        coach_user = User.objects.create_user(email="coach-team-delete@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        position = Position.objects.create(club=self.club, name="CoachTeamDelete", short_name="CTD", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=position)
        self.client.force_login(coach_user)

        response = self.club_post("team_delete", {}, team.pk)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Team.objects.filter(pk=team.pk).exists())


class PositionManagementTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)

    def test_position_list_is_scoped_to_the_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        Position.objects.create(club=other_club, name="Rival Coach", short_name="RC", staff_position=True)

        response = self.club_get("position_list")

        self.assertNotContains(response, "Rival Coach")

    def test_creating_a_position(self):
        response = self.club_post("position_create", {"name": "Physio", "short_name": "PH", "ordering": 0, "staff_position": "on", "management_position": ""})

        position = Position.objects.get(club=self.club, name="Physio")
        self.assertRedirects(response, reverse("management:position_list"))
        self.assertTrue(position.staff_position)
        self.assertFalse(position.management_position)

    def test_updating_a_position(self):
        position = Position.objects.create(club=self.club, name="Old name", short_name="ON")

        self.club_post("position_update", {"name": "New name", "short_name": "NN", "ordering": 0}, position.pk)

        position.refresh_from_db()
        self.assertEqual(position.name, "New name")

    def test_a_management_position_must_also_be_a_staff_position(self):
        response = self.club_post("position_create", {"name": "Bad", "short_name": "B", "ordering": 0, "management_position": "on"})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Position.objects.filter(club=self.club, name="Bad").exists())
        self.assertFormError(response.context["form"], "management_position", "A management position must also be a staff position.")


class ClubRoleManagementTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)
        self.member = Member.objects.create(first_name="Future", last_name="Editor")
        # An active membership already grants an implicit MEMBER role (club/signals.py) --
        # granting EDITOR must promote that row, not insert a second one.
        ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

    def test_granting_a_role_promotes_the_existing_membership_role(self):
        self.club_post("role_create", {"member": str(self.member.pk), "role": ClubRole.Roles.EDITOR})

        role = ClubRole.objects.get(club=self.club, member=self.member)
        self.assertEqual(role.role, ClubRole.Roles.EDITOR)
        self.assertEqual(ClubRole.objects.filter(club=self.club, member=self.member).count(), 1)

    def test_revoking_a_role(self):
        role = ClubRole.objects.get(club=self.club, member=self.member)

        self.club_post("role_revoke", {}, role.pk)

        self.assertFalse(ClubRole.objects.filter(pk=role.pk).exists())

    def test_the_plain_member_role_never_appears_on_the_list(self):
        # self.admin_member and self.member both hold an implicit MEMBER role --
        # noise this page must never show. (self.member's name still legitimately
        # appears once, in the "Grant role" modal's member picker.)
        response = self.club_get("role_list")

        self.assertContains(response, "No one has the Editor role yet.")

    def test_a_granted_role_appears_under_its_own_section(self):
        self.club_post("role_create", {"member": str(self.member.pk), "role": ClubRole.Roles.EDITOR})

        response = self.club_get("role_list")

        self.assertContains(response, "Future Editor")

    def test_grant_role_is_a_modal_on_the_list_page(self):
        response = self.club_get("role_list")

        self.assertContains(response, 'id="grant_role_modal"')

    def test_each_section_explains_what_the_role_grants(self):
        response = self.club_get("role_list")

        self.assertContains(response, "Full control over the club")
        self.assertContains(response, "Can create and edit events")

    def test_an_invalid_submission_redirects_back_to_the_list_instead_of_a_page(self):
        response = self.club_post("role_create", {"member": "", "role": ClubRole.Roles.EDITOR})

        self.assertRedirects(response, reverse("management:role_list"))


class FamilyManagementTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)

    def test_registering_a_family_creates_a_login_parent_and_a_login_less_child(self):
        response = self.club_post(
            "family_create",
            {
                "parent_first_name": "Pat",
                "parent_last_name": "Parent",
                "parent_email": "pat.parent@example.com",
                "child_first_name": "Cody",
                "child_last_name": "Child",
                "child_date_of_birth": "2015-04-01",
            },
        )

        family = Family.objects.get(memberships__member__first_name="Cody")
        self.assertRedirects(response, reverse("management:family_detail", args=[family.pk]))

        parent = Member.objects.get(first_name="Pat", last_name="Parent")
        child = Member.objects.get(first_name="Cody", last_name="Child")
        self.assertEqual(parent.user.email, "pat.parent@example.com")
        self.assertFalse(parent.user.has_usable_password(), "should set a password via the reset link, not be given one")
        self.assertIsNone(child.user)

        self.assertEqual(FamilyMembership.objects.get(family=family, member=parent).role, FamilyMembership.FamilyRole.PARENT)
        self.assertEqual(FamilyMembership.objects.get(family=family, member=child).role, FamilyMembership.FamilyRole.CHILD)

        # Both get signed up for the current season, same as a plain MemberCreateView.
        self.assertTrue(ClubMembership.objects.filter(club=self.club, member=parent, season=self.season).exists())
        self.assertTrue(ClubMembership.objects.filter(club=self.club, member=child, season=self.season).exists())

    def test_reusing_an_existing_login_by_email(self):
        # A parent who's already a Member elsewhere (an existing login) must be
        # reused, not duplicated, when registered onto a second family.
        existing_user = User.objects.create_user(email="existing@example.com", password="pw-secret-123")
        existing_member = Member.objects.create(user=existing_user, first_name="Existing", last_name="Parent")

        self.club_post(
            "family_create",
            {
                "parent_first_name": "Ignored",
                "parent_last_name": "Ignored",
                "parent_email": "existing@example.com",
                "child_first_name": "New",
                "child_last_name": "Kid",
            },
        )

        self.assertEqual(Member.objects.filter(user=existing_user).count(), 1)
        family = Family.objects.get(memberships__member__first_name="New")
        self.assertIn(existing_member, family.guardians)

    def make_existing_family(self):
        # A family only counts as "of this club" once at least one of its members
        # has actually signed up (families_of_club, management/views.py) -- exactly
        # what registering the first child through this app already does.
        family = Family.objects.create()
        first_kid = Member.objects.create(first_name="First", last_name="Kid")
        FamilyMembership.objects.create(family=family, member=first_kid, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=first_kid, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        return family

    def test_adding_a_child_to_an_existing_family(self):
        family = self.make_existing_family()

        response = self.club_post("family_add_child", {"first_name": "Second", "last_name": "Kid", "date_of_birth": "2018-01-01"}, family.pk)

        self.assertRedirects(response, reverse("management:family_detail", args=[family.pk]))
        self.assertEqual(family.children.count(), 2)
        self.assertTrue(ClubMembership.objects.filter(club=self.club, member__first_name="Second", season=self.season).exists())

    def test_adding_a_parent_to_an_existing_family_is_idempotent(self):
        family = self.make_existing_family()

        self.club_post("family_add_parent", {"email": "new.parent@example.com", "first_name": "New", "last_name": "Parent"}, family.pk)
        self.club_post("family_add_parent", {"email": "new.parent@example.com", "first_name": "", "last_name": ""}, family.pk)

        self.assertEqual(family.guardians.count(), 1)


class MemberListFamilyColumnTests(ManagementTestBase):
    """The member list is one flat table -- family is a column (each member's family/
    role attached in Python, management.views.MemberListView), not a grouping."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)

    def make_family(self, parent_name, child_name):
        family = Family.objects.create()
        parent = Member.objects.create(first_name=parent_name, last_name="Guardian")
        child = Member.objects.create(first_name=child_name, last_name="Kid")
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=parent, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        ClubMembership.objects.create(club=self.club, member=child, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        return family, parent, child

    def test_a_family_renders_as_one_group_on_the_member_list(self):
        family, parent, child = self.make_family("Pat", "Cody")

        response = self.club_get("member_list")

        self.assertContains(response, str(family))
        self.assertContains(response, str(parent))
        self.assertContains(response, str(child))

    def test_a_member_with_no_family_has_an_empty_family_column(self):
        loner = Member.objects.create(first_name="Lone", last_name="Member")
        ClubMembership.objects.create(club=self.club, member=loner, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

        response = self.club_get("member_list")

        members_by_pk = {member.pk: member for member in response.context["members"]}
        self.assertEqual(members_by_pk[loner.pk].family_memberships_display, [])

    def test_a_family_members_column_shows_the_family_and_role(self):
        family, parent, child = self.make_family("Pat", "Cody")

        response = self.club_get("member_list")

        members_by_pk = {member.pk: member for member in response.context["members"]}
        parent_fms = members_by_pk[parent.pk].family_memberships_display
        child_fms = members_by_pk[child.pk].family_memberships_display
        self.assertEqual([fm.family for fm in parent_fms], [family])
        self.assertEqual(parent_fms[0].role, "parent")
        self.assertEqual(child_fms[0].role, "child")

    def test_a_member_in_two_families_shows_both_on_the_member_list(self):
        family, parent, _child = self.make_family("Pat", "Cody")
        other_family = Family.objects.create()
        FamilyMembership.objects.create(family=other_family, member=parent, role=FamilyMembership.FamilyRole.OTHER)

        response = self.club_get("member_list")

        members_by_pk = {member.pk: member for member in response.context["members"]}
        parent_fms = members_by_pk[parent.pk].family_memberships_display
        self.assertEqual({fm.family for fm in parent_fms}, {family, other_family})

    def test_families_of_other_clubs_do_not_appear(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        other_season = make_season(other_club)
        other_member = Member.objects.create(first_name="Other", last_name="Kid")
        ClubMembership.objects.create(club=other_club, member=other_member, season=other_season, status=ClubMembership.StatusChoices.ACTIVE)
        other_family = Family.objects.create()
        FamilyMembership.objects.create(family=other_family, member=other_member, role=FamilyMembership.FamilyRole.CHILD)

        response = self.club_get("member_list")

        self.assertNotContains(response, str(other_family))
        self.assertNotContains(response, "Other Kid")

    def test_a_non_admin_never_sees_a_family_mate_outside_their_own_visibility(self):
        # group_by_family must bucket the already-scoped members_visible_to() result,
        # never Family.guardians/.children directly -- those ignore scoping entirely
        # and would leak a family-mate a non-admin has no other reason to see.
        _family, parent, child = self.make_family("Pat", "Cody")

        coach_user = User.objects.create_user(email="coach3@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        team = Team.objects.create(club=self.club, name="U14", short_name="U14")
        position = Position.objects.create(club=self.club, name="Coach3", short_name="C3", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=position)
        self.client.force_login(coach_user)

        response = self.club_get("member_list")

        self.assertNotContains(response, str(parent))
        self.assertNotContains(response, str(child))


class MemberListStatusColumnTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)

    def test_current_season_status_is_shown(self):
        member = Member.objects.create(first_name="Sam", last_name="Pending")
        ClubMembership.objects.create(club=self.club, member=member, season=self.season, status=ClubMembership.StatusChoices.PENDING)

        response = self.club_get("member_list")

        members_by_pk = {m.pk: m for m in response.context["members"]}
        self.assertEqual(members_by_pk[member.pk].current_membership.status, ClubMembership.StatusChoices.PENDING)
        self.assertContains(response, "Pending")

    def test_a_member_with_no_current_season_membership_shows_a_dash(self):
        ClubMembership.objects.filter(club=self.club, member=self.admin_member).delete()

        response = self.club_get("member_list")

        members_by_pk = {m.pk: m for m in response.context["members"]}
        self.assertIsNone(members_by_pk[self.admin_member.pk].current_membership)

    def test_a_lapsed_season_membership_does_not_count_as_current(self):
        lapsed_season = Season.objects.create(club=self.club, start_date=self.season.start_date - datetime.timedelta(days=400), end_date=self.season.start_date - datetime.timedelta(days=40))
        member = Member.objects.create(first_name="Old", last_name="Season")
        ClubMembership.objects.create(club=self.club, member=member, season=lapsed_season, status=ClubMembership.StatusChoices.ACTIVE)

        response = self.club_get("member_list")

        members_by_pk = {m.pk: m for m in response.context["members"]}
        self.assertIsNone(members_by_pk[member.pk].current_membership)


class MemberGrantLoginTests(ManagementTestBase):
    """A login-less child getting their own account -- see
    members.services.family.grant_login and management.forms.GrantLoginForm."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)
        self.family = Family.objects.create()
        self.child = Member.objects.create(first_name="Cody", last_name="Kid")
        FamilyMembership.objects.create(family=self.family, member=self.child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=self.child, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

    def test_family_page_offers_the_button_for_a_login_less_child(self):
        response = self.club_get("family_detail", self.family.pk)

        self.assertContains(response, reverse("management:member_grant_login", args=[self.child.pk]))

    def test_no_button_once_the_child_already_has_a_login(self):
        self.child.user = User.objects.create_user(email="already@example.com", password="pw-secret-123")
        self.child.save()

        response = self.club_get("family_detail", self.family.pk)

        self.assertNotContains(response, reverse("management:member_grant_login", args=[self.child.pk]))

    def test_no_button_for_a_parent(self):
        parent = Member.objects.create(first_name="Pat", last_name="Parent")
        FamilyMembership.objects.create(family=self.family, member=parent, role=FamilyMembership.FamilyRole.PARENT)
        ClubMembership.objects.create(club=self.club, member=parent, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

        response = self.club_get("family_detail", self.family.pk)

        self.assertNotContains(response, reverse("management:member_grant_login", args=[parent.pk]))

    def test_the_form_is_prefilled_with_the_childs_contact_email_if_set(self):
        self.child.email = "cody.kid@example.com"
        self.child.save()

        response = self.club_get("family_detail", self.family.pk)

        self.assertContains(response, "cody.kid@example.com")

    def test_granting_a_login_creates_a_usable_account(self):
        response = self.club_post("member_grant_login", {"email": "cody.kid@example.com"}, self.child.pk)

        self.assertRedirects(response, reverse("management:member_detail", args=[self.child.pk]))
        self.child.refresh_from_db()
        self.assertIsNotNone(self.child.user)
        self.assertEqual(self.child.user.email, "cody.kid@example.com")
        self.assertFalse(self.child.user.has_usable_password())

    def test_an_email_already_in_use_is_rejected(self):
        User.objects.create_user(email="taken@example.com", password="pw-secret-123")

        response = self.club_post("member_grant_login", {"email": "taken@example.com"}, self.child.pk)

        self.assertRedirects(response, reverse("management:member_detail", args=[self.child.pk]))
        self.child.refresh_from_db()
        self.assertIsNone(self.child.user)

    def test_non_admin_gets_403(self):
        coach_user = User.objects.create_user(email="coach-grant@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        team = Team.objects.create(club=self.club, name="U18", short_name="U18")
        position = Position.objects.create(club=self.club, name="Coach7", short_name="C7", staff_position=True)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=position)
        self.client.force_login(coach_user)

        response = self.club_post("member_grant_login", {"email": "cody.kid@example.com"}, self.child.pk)

        self.assertEqual(response.status_code, 403)
        self.child.refresh_from_db()
        self.assertIsNone(self.child.user)


class MemberFamilyAttachDetachTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)
        self.standalone = Member.objects.create(first_name="Stan", last_name="Alone")
        ClubMembership.objects.create(club=self.club, member=self.standalone, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

    def test_attaching_to_a_new_family(self):
        response = self.club_post("member_attach_family", {"role": FamilyMembership.FamilyRole.PARENT, "family": ""}, self.standalone.pk)

        self.assertRedirects(response, reverse("management:member_detail", args=[self.standalone.pk]))
        family = Family.objects.get(memberships__member=self.standalone)
        self.assertIn(self.standalone, family.guardians)

    def test_attaching_to_an_existing_family(self):
        family = Family.objects.create()
        kid = Member.objects.create(first_name="Existing", last_name="Kid")
        ClubMembership.objects.create(club=self.club, member=kid, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        FamilyMembership.objects.create(family=family, member=kid, role=FamilyMembership.FamilyRole.CHILD)

        self.club_post("member_attach_family", {"role": FamilyMembership.FamilyRole.PARENT, "family": str(family.pk)}, self.standalone.pk)

        self.assertIn(self.standalone, family.guardians)
        self.assertEqual(Family.objects.filter(memberships__member=self.standalone).count(), 1)

    def test_detaching_from_family_removes_it_when_left_empty(self):
        family = Family.objects.create()
        FamilyMembership.objects.create(family=family, member=self.standalone, role=FamilyMembership.FamilyRole.CHILD)

        response = self.club_post("member_detach_family", {}, self.standalone.pk, family.pk)

        self.assertRedirects(response, reverse("management:member_detail", args=[self.standalone.pk]))
        self.assertFalse(FamilyMembership.objects.filter(member=self.standalone).exists())
        self.assertFalse(Family.objects.filter(pk=family.pk).exists())

    def test_detaching_from_family_keeps_it_when_others_remain(self):
        family = Family.objects.create()
        sibling = Member.objects.create(first_name="Sibling", last_name="Kid")
        ClubMembership.objects.create(club=self.club, member=sibling, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        FamilyMembership.objects.create(family=family, member=self.standalone, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=family, member=sibling, role=FamilyMembership.FamilyRole.CHILD)

        self.club_post("member_detach_family", {}, self.standalone.pk, family.pk)

        self.assertTrue(Family.objects.filter(pk=family.pk).exists())
        self.assertIn(sibling, family.children)

    def test_detaching_from_one_family_keeps_membership_in_another(self):
        family = Family.objects.create()
        other_family = Family.objects.create()
        FamilyMembership.objects.create(family=family, member=self.standalone, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=other_family, member=self.standalone, role=FamilyMembership.FamilyRole.OTHER)

        self.club_post("member_detach_family", {}, self.standalone.pk, family.pk)

        self.assertFalse(FamilyMembership.objects.filter(member=self.standalone, family=family).exists())
        self.assertTrue(FamilyMembership.objects.filter(member=self.standalone, family=other_family).exists())

    def test_a_member_can_join_a_second_family(self):
        family = Family.objects.create()
        FamilyMembership.objects.create(family=family, member=self.standalone, role=FamilyMembership.FamilyRole.CHILD)

        response = self.club_post("member_attach_family", {"role": FamilyMembership.FamilyRole.OTHER, "family": ""}, self.standalone.pk)

        self.assertRedirects(response, reverse("management:member_detail", args=[self.standalone.pk]))
        self.assertEqual(FamilyMembership.objects.filter(member=self.standalone).count(), 2)
        self.assertTrue(FamilyMembership.objects.filter(member=self.standalone, family=family).exists())

    def test_member_detail_shows_a_card_per_family(self):
        family = Family.objects.create()
        other_family = Family.objects.create()
        FamilyMembership.objects.create(family=family, member=self.standalone, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=other_family, member=self.standalone, role=FamilyMembership.FamilyRole.OTHER)

        response = self.club_get("member_detail", self.standalone.pk)

        self.assertEqual(len(response.context["family_groups"]), 2)
        self.assertContains(response, str(family))
        self.assertContains(response, str(other_family))
        # "Add to family" stays available even though the member is already in two.
        self.assertContains(response, "Add to family")

    def test_attach_form_excludes_families_already_joined(self):
        family = Family.objects.create()
        FamilyMembership.objects.create(family=family, member=self.standalone, role=FamilyMembership.FamilyRole.CHILD)

        response = self.club_get("member_detail", self.standalone.pk)

        self.assertNotIn(family, response.context["attach_to_family_form"].fields["family"].queryset)


class MemberDeleteTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)
        self.member = Member.objects.create(first_name="Doomed", last_name="Member")
        ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

    def test_deleting_a_member(self):
        response = self.club_post("member_delete", {}, self.member.pk)

        self.assertRedirects(response, reverse("management:member_list"))
        self.assertFalse(Member.objects.filter(pk=self.member.pk).exists())

    def test_deleting_the_last_member_of_a_family_cleans_it_up(self):
        family = Family.objects.create()
        FamilyMembership.objects.create(family=family, member=self.member, role=FamilyMembership.FamilyRole.CHILD)

        self.club_post("member_delete", {}, self.member.pk)

        self.assertFalse(Family.objects.filter(pk=family.pk).exists())

    def test_a_member_referenced_by_an_order_cannot_be_deleted(self):
        # shop.Order.purchaser is PROTECT -- deleting must fail gracefully, not 500.
        Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("10.00"))

        response = self.club_post("member_delete", {}, self.member.pk)

        self.assertRedirects(response, reverse("management:member_detail", args=[self.member.pk]))
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())


class MemberClubMembershipFormTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)
        self.member = Member.objects.create(first_name="Fee", last_name="Payer")
        self.membership = ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

    def test_editing_a_member_also_updates_their_current_membership(self):
        response = self.club_post(
            "member_update",
            {
                "first_name": "Fee",
                "last_name": "Payer",
                "license": "BE-9999",
                "status": ClubMembership.StatusChoices.ACTIVE,
                "fee_status": ClubMembership.FeeStatus.PAID,
            },
            self.member.pk,
        )

        self.assertRedirects(response, reverse("management:member_detail", args=[self.member.pk]))
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.license, "BE-9999")
        self.assertEqual(self.membership.fee_status, ClubMembership.FeeStatus.PAID)

    def test_membership_section_is_fillable_when_not_yet_rostered_this_season(self):
        member = Member.objects.create(first_name="Unrostered", last_name="Member")
        # Visible (has *a* membership in this club), but not for the current season --
        # this is exactly the case that should now offer a fillable section to sign
        # them up, rather than hiding it because nothing exists yet.
        old_season = Season.objects.create(club=self.club, start_date=datetime.date(2020, 8, 1), end_date=datetime.date(2021, 5, 31))
        ClubMembership.objects.create(club=self.club, member=member, season=old_season, status=ClubMembership.StatusChoices.LAPSED)

        response = self.club_get("member_update", member.pk)

        self.assertContains(response, "This season")
        self.assertFalse(ClubMembership.objects.filter(club=self.club, member=member, season=self.season).exists())

    def test_saving_the_membership_section_signs_up_a_previously_unrostered_member(self):
        member = Member.objects.create(first_name="Unrostered", last_name="Member")
        # A ClubRole, not a ClubMembership -- gives the admin visibility into this
        # member without a season-bound row already existing, which is exactly the
        # "not signed up for anything yet" case this test means to exercise.
        ClubRole.objects.create(club=self.club, member=member, role=ClubRole.Roles.MEMBER)

        response = self.club_post(
            "member_update",
            {"first_name": "Unrostered", "last_name": "Member", "status": ClubMembership.StatusChoices.ACTIVE, "fee_status": ClubMembership.FeeStatus.PAID},
            member.pk,
        )

        self.assertRedirects(response, reverse("management:member_detail", args=[member.pk]))
        membership = ClubMembership.objects.get(club=self.club, member=member, season=self.season)
        self.assertEqual(membership.status, ClubMembership.StatusChoices.ACTIVE)
        self.assertEqual(membership.fee_status, ClubMembership.FeeStatus.PAID)

    def test_membership_section_absent_with_no_season_at_all(self):
        # A fresh club, never given a season -- there's nothing sensible to sign up
        # for, so unlike the "not rostered yet" case above, this stays hidden.
        empty_club = Club.objects.create(name="No Season FC", slug="no-season-fc")
        admin_member = Member.objects.create(first_name="Empty", last_name="Admin")
        ClubRole.objects.create(club=empty_club, member=admin_member, role=ClubRole.Roles.ADMIN)
        admin_user = User.objects.create_user(email="noseasonadmin@example.com", password="pw-secret-123")
        admin_member.user = admin_user
        admin_member.save()
        enrol_mfa(admin_user)
        self.client.force_login(admin_user)
        member = Member.objects.create(first_name="No", last_name="Season")
        ClubRole.objects.create(club=empty_club, member=member, role=ClubRole.Roles.MEMBER)

        with override_settings(ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "no-season-fc.rosterchief.app", "testserver"]):
            response = self.client.get(reverse("management:member_update", args=[member.pk]), HTTP_HOST="no-season-fc.rosterchief.app")

        self.assertNotContains(response, "This season")

    def test_detail_page_shows_season_history(self):
        old_season = Season.objects.create(club=self.club, start_date=datetime.date(2020, 8, 1), end_date=datetime.date(2021, 5, 31))
        ClubMembership.objects.create(club=self.club, member=self.member, season=old_season, status=ClubMembership.StatusChoices.LAPSED, fee_status=ClubMembership.FeeStatus.UNPAID)

        response = self.club_get("member_detail", self.member.pk)

        self.assertContains(response, f"{self.season.start_date:%Y} - {self.season.end_date:%Y}")
        self.assertContains(response, f"{old_season.start_date:%Y} - {old_season.end_date:%Y}")
        self.assertContains(response, "lapsed")

    def test_formatted_phone_number_is_shown(self):
        self.member.phone = "+32476123456"
        self.member.save()

        response = self.club_get("member_detail", self.member.pk)

        self.assertContains(response, "+32 476 12 34 56")


class FamilyDetailViewTests(ManagementTestBase):
    """The family overview page, reached by clicking a family's name -- there's no
    standalone "Families" nav entry or list."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)
        self.family = Family.objects.create()
        self.parent = Member.objects.create(first_name="Pat", last_name="Guardian")
        self.child = Member.objects.create(first_name="Cody", last_name="Kid")
        FamilyMembership.objects.create(family=self.family, member=self.parent, role=FamilyMembership.FamilyRole.PARENT)
        FamilyMembership.objects.create(family=self.family, member=self.child, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=self.parent, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        ClubMembership.objects.create(club=self.club, member=self.child, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

    def test_shows_every_member_of_the_family(self):
        response = self.club_get("family_detail", self.family.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Guardian")
        self.assertContains(response, "Kid")

    def test_a_family_from_another_club_404s(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        other_family = Family.objects.create()
        other_member = Member.objects.create(first_name="Other", last_name="Kid")
        other_season = make_season(other_club)
        ClubMembership.objects.create(club=other_club, member=other_member, season=other_season, status=ClubMembership.StatusChoices.ACTIVE)
        FamilyMembership.objects.create(family=other_family, member=other_member, role=FamilyMembership.FamilyRole.CHILD)

        response = self.club_get("family_detail", other_family.pk)

        self.assertEqual(response.status_code, 404)

    def test_only_admins_see_edit_and_delete_actions(self):
        # Give the coach visibility into this family's child (rostered on their team),
        # so the assertion below actually exercises the admin-only button gating --
        # not just "the coach can't see this row at all".
        coach_user = User.objects.create_user(email="coach4@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        team = Team.objects.create(club=self.club, name="U15", short_name="U15")
        staff_position = Position.objects.create(club=self.club, name="Coach4", short_name="C4", staff_position=True, management_position=True)
        player_position = Position.objects.create(club=self.club, name="Player4", short_name="P4", staff_position=False)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=staff_position)
        TeamMembership.objects.create(team=team, member=self.child, season=self.season, position=player_position)
        self.client.force_login(coach_user)

        response = self.club_get("family_detail", self.family.pk)

        self.assertContains(response, "Kid")
        # The edit icon links straight to member_detail, same as the row's own name
        # link -- not admin-exclusive, so member_delete is the actual gating signal.
        self.assertNotContains(response, reverse("management:member_delete", args=[self.child.pk]))

    def test_admin_sees_a_remove_from_family_action_per_row(self):
        response = self.club_get("family_detail", self.family.pk)

        self.assertContains(response, reverse("management:member_detach_family", args=[self.parent.pk, self.family.pk]))
        self.assertContains(response, reverse("management:member_detach_family", args=[self.child.pk, self.family.pk]))

    def test_non_admin_does_not_see_the_remove_from_family_action(self):
        coach_user = User.objects.create_user(email="coach6@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        team = Team.objects.create(club=self.club, name="U17", short_name="U17")
        staff_position = Position.objects.create(club=self.club, name="Coach6", short_name="C6", staff_position=True, management_position=True)
        player_position = Position.objects.create(club=self.club, name="Player6", short_name="P6", staff_position=False)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=staff_position)
        TeamMembership.objects.create(team=team, member=self.child, season=self.season, position=player_position)
        self.client.force_login(coach_user)

        response = self.club_get("family_detail", self.family.pk)

        self.assertNotContains(response, reverse("management:member_detach_family", args=[self.child.pk, self.family.pk]))

    def test_removing_a_family_member_from_the_row_button_detaches_only_them(self):
        response = self.club_post("member_detach_family", {}, self.child.pk, self.family.pk)

        self.assertRedirects(response, reverse("management:member_detail", args=[self.child.pk]))
        self.assertFalse(FamilyMembership.objects.filter(family=self.family, member=self.child).exists())
        self.assertTrue(FamilyMembership.objects.filter(family=self.family, member=self.parent).exists())


class FamilyMembershipRoleUpdateTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)
        self.family = Family.objects.create()
        self.member = Member.objects.create(first_name="Cody", last_name="Kid")
        self.membership = FamilyMembership.objects.create(family=self.family, member=self.member, role=FamilyMembership.FamilyRole.CHILD)
        ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

    def test_admin_can_change_the_role(self):
        response = self.club_post("family_membership_role_update", {"role": FamilyMembership.FamilyRole.GUARDIAN}, self.family.pk, self.member.pk)

        self.assertRedirects(response, reverse("management:family_detail", args=[self.family.pk]))
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.role, FamilyMembership.FamilyRole.GUARDIAN)

    def test_only_changes_the_role_in_the_targeted_family(self):
        other_family = Family.objects.create()
        other_membership = FamilyMembership.objects.create(family=other_family, member=self.member, role=FamilyMembership.FamilyRole.OTHER)

        self.club_post("family_membership_role_update", {"role": FamilyMembership.FamilyRole.PARENT}, self.family.pk, self.member.pk)

        self.membership.refresh_from_db()
        other_membership.refresh_from_db()
        self.assertEqual(self.membership.role, FamilyMembership.FamilyRole.PARENT)
        self.assertEqual(other_membership.role, FamilyMembership.FamilyRole.OTHER)

    def test_an_invalid_role_is_rejected(self):
        response = self.club_post("family_membership_role_update", {"role": "not-a-role"}, self.family.pk, self.member.pk)

        self.assertRedirects(response, reverse("management:family_detail", args=[self.family.pk]))
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.role, FamilyMembership.FamilyRole.CHILD)

    def test_non_admin_gets_403(self):
        coach_user = User.objects.create_user(email="coach-role@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        position = Position.objects.create(club=self.club, name="Coach5", short_name="C5", staff_position=True)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=position)
        self.client.force_login(coach_user)

        response = self.club_post("family_membership_role_update", {"role": FamilyMembership.FamilyRole.PARENT}, self.family.pk, self.member.pk)

        self.assertEqual(response.status_code, 403)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.role, FamilyMembership.FamilyRole.CHILD)

    def test_a_family_from_another_club_404s(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        other_season = make_season(other_club)
        other_family = Family.objects.create()
        other_member = Member.objects.create(first_name="Other", last_name="Kid")
        ClubMembership.objects.create(club=other_club, member=other_member, season=other_season, status=ClubMembership.StatusChoices.ACTIVE)
        FamilyMembership.objects.create(family=other_family, member=other_member, role=FamilyMembership.FamilyRole.CHILD)

        response = self.club_post("family_membership_role_update", {"role": FamilyMembership.FamilyRole.PARENT}, other_family.pk, other_member.pk)

        self.assertEqual(response.status_code, 404)

    def test_the_dropdown_reflects_the_role_specific_to_that_family(self):
        # Same member, a different role in a second family -- the "others" bucket
        # used to read person.family_memberships.first(), which could silently show
        # a role from the wrong family. This is exactly that scenario.
        other_family = Family.objects.create()
        FamilyMembership.objects.create(family=other_family, member=self.member, role=FamilyMembership.FamilyRole.OTHER)

        this_family_response = self.club_get("family_detail", self.family.pk)
        other_family_response = self.club_get("family_detail", other_family.pk)

        self.assertContains(this_family_response, 'value="child" selected')
        self.assertContains(other_family_response, 'value="other" selected')

    def test_redirects_to_next_when_changed_from_the_member_page(self):
        next_url = reverse("management:member_detail", args=[self.member.pk])

        response = self.club_post("family_membership_role_update", {"role": FamilyMembership.FamilyRole.GUARDIAN, "next": next_url}, self.family.pk, self.member.pk)

        self.assertRedirects(response, next_url)

    def test_ignores_an_unsafe_next_url(self):
        response = self.club_post(
            "family_membership_role_update", {"role": FamilyMembership.FamilyRole.GUARDIAN, "next": "https://evil.example.com/steal"}, self.family.pk, self.member.pk
        )

        self.assertRedirects(response, reverse("management:family_detail", args=[self.family.pk]))

    def test_member_detail_page_sends_its_own_url_as_next(self):
        response = self.club_get("member_detail", self.member.pk)

        self.assertContains(response, f'name="next" value="{reverse("management:member_detail", args=[self.member.pk])}"')

    def test_family_detail_page_sends_no_next(self):
        response = self.club_get("family_detail", self.family.pk)

        self.assertNotContains(response, 'name="next"')


class MembershipListViewTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)
        # The base class's own admin ClubMembership (fee_status defaults to UNPAID)
        # would otherwise pollute every count below -- ClubRole (not ClubMembership)
        # is what actually makes them an admin, so this is safe to drop.
        ClubMembership.objects.filter(club=self.club, member=self.admin_member).delete()

    def make_membership(self, first_name, last_name, *, status=ClubMembership.StatusChoices.ACTIVE, fee_status=ClubMembership.FeeStatus.UNPAID, season=None, license=""):
        member = Member.objects.create(first_name=first_name, last_name=last_name)
        return ClubMembership.objects.create(club=self.club, member=member, season=season or self.season, status=status, fee_status=fee_status, license=license)

    def test_kpi_counts_for_the_current_season(self):
        self.make_membership("Paid", "One", fee_status=ClubMembership.FeeStatus.PAID)
        self.make_membership("Partial", "One", fee_status=ClubMembership.FeeStatus.PARTIALLY_PAID)
        self.make_membership("Unpaid", "One", fee_status=ClubMembership.FeeStatus.UNPAID)
        self.make_membership("Unpaid", "Two", fee_status=ClubMembership.FeeStatus.UNPAID)
        self.make_membership("Waived", "One", fee_status=ClubMembership.FeeStatus.WAIVED)

        response = self.club_get("membership_list")

        self.assertEqual(response.context["kpi_total"], 5)
        self.assertEqual(response.context["kpi_paid"], 1)
        self.assertEqual(response.context["kpi_partial"], 1)
        self.assertEqual(response.context["kpi_unpaid"], 2)
        self.assertEqual(response.context["kpi_waived"], 1)
        self.assertEqual(response.context["kpi_paid_rate"], 20)

    def test_default_list_excludes_paid_but_includes_waived(self):
        paid = self.make_membership("Paid", "One", fee_status=ClubMembership.FeeStatus.PAID)
        unpaid = self.make_membership("Unpaid", "One", fee_status=ClubMembership.FeeStatus.UNPAID)
        waived = self.make_membership("Waived", "One", fee_status=ClubMembership.FeeStatus.WAIVED)

        response = self.club_get("membership_list")

        ids = {m.pk for m in response.context["memberships"]}
        self.assertNotIn(paid.pk, ids)
        self.assertIn(unpaid.pk, ids)
        self.assertIn(waived.pk, ids)

    def test_fee_status_filter_narrows_to_one_value(self):
        unpaid = self.make_membership("Unpaid", "One", fee_status=ClubMembership.FeeStatus.UNPAID)
        waived = self.make_membership("Waived", "One", fee_status=ClubMembership.FeeStatus.WAIVED)

        response = self.client.get(reverse("management:membership_list") + "?fee_status=waived", HTTP_HOST="ajax-united.rosterchief.app")

        ids = {m.pk for m in response.context["memberships"]}
        self.assertNotIn(unpaid.pk, ids)
        self.assertIn(waived.pk, ids)

    def test_fee_status_all_includes_paid(self):
        paid = self.make_membership("Paid", "One", fee_status=ClubMembership.FeeStatus.PAID)

        response = self.client.get(reverse("management:membership_list") + "?fee_status=all", HTTP_HOST="ajax-united.rosterchief.app")

        ids = {m.pk for m in response.context["memberships"]}
        self.assertIn(paid.pk, ids)

    def test_status_filter(self):
        active = self.make_membership("Active", "One", status=ClubMembership.StatusChoices.ACTIVE)
        pending = self.make_membership("Pending", "One", status=ClubMembership.StatusChoices.PENDING)

        response = self.client.get(reverse("management:membership_list") + "?status=pending", HTTP_HOST="ajax-united.rosterchief.app")

        ids = {m.pk for m in response.context["memberships"]}
        self.assertNotIn(active.pk, ids)
        self.assertIn(pending.pk, ids)

    def test_team_filter(self):
        on_team = self.make_membership("OnTeam", "Kid")
        off_team = self.make_membership("OffTeam", "Kid")
        team = Team.objects.create(club=self.club, name="U10", short_name="U10")
        position = Position.objects.create(club=self.club, name="Player", short_name="P")
        TeamMembership.objects.create(team=team, member=on_team.member, season=self.season, position=position)

        response = self.client.get(reverse("management:membership_list") + f"?team={team.pk}", HTTP_HOST="ajax-united.rosterchief.app")

        ids = {m.pk for m in response.context["memberships"]}
        self.assertIn(on_team.pk, ids)
        self.assertNotIn(off_team.pk, ids)

    def test_search_filter(self):
        match = self.make_membership("Findme", "Person")
        other = self.make_membership("Other", "Person")

        response = self.client.get(reverse("management:membership_list") + "?q=Findme", HTTP_HOST="ajax-united.rosterchief.app")

        ids = {m.pk for m in response.context["memberships"]}
        self.assertIn(match.pk, ids)
        self.assertNotIn(other.pk, ids)

    def test_search_matches_by_family_surname(self):
        # Searching "Smith" should find a family member even when their own name
        # isn't Smith -- e.g. a parent with a different surname than their kid.
        family = Family.objects.create()
        smith_kid = self.make_membership("Junior", "Smith")
        FamilyMembership.objects.create(family=family, member=smith_kid.member, role=FamilyMembership.FamilyRole.CHILD)
        other_parent = self.make_membership("Alex", "Jones")
        FamilyMembership.objects.create(family=family, member=other_parent.member, role=FamilyMembership.FamilyRole.PARENT)
        unrelated = self.make_membership("Nobody", "Related")

        response = self.client.get(reverse("management:membership_list") + "?q=Smith", HTTP_HOST="ajax-united.rosterchief.app")

        ids = {m.pk for m in response.context["memberships"]}
        self.assertIn(smith_kid.pk, ids)
        self.assertIn(other_parent.pk, ids)
        self.assertNotIn(unrelated.pk, ids)

    def test_search_matches_an_explicit_family_name(self):
        family = Family.objects.create(name="The Andersons")
        membership = self.make_membership("Pat", "Vandermeer")
        FamilyMembership.objects.create(family=family, member=membership.member, role=FamilyMembership.FamilyRole.PARENT)
        unrelated = self.make_membership("Nobody", "Related")

        response = self.client.get(reverse("management:membership_list") + "?q=Anderson", HTTP_HOST="ajax-united.rosterchief.app")

        ids = {m.pk for m in response.context["memberships"]}
        self.assertIn(membership.pk, ids)
        self.assertNotIn(unrelated.pk, ids)

    def test_family_column_shows_every_family_a_member_belongs_to(self):
        family = Family.objects.create()
        other_family = Family.objects.create()
        membership = self.make_membership("Multi", "Family")
        FamilyMembership.objects.create(family=family, member=membership.member, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=other_family, member=membership.member, role=FamilyMembership.FamilyRole.OTHER)

        response = self.club_get("membership_list")

        by_pk = {m.pk: m for m in response.context["memberships"]}
        family_ids = {fm.family_id for fm in by_pk[membership.pk].member.family_memberships_display}
        self.assertEqual(family_ids, {family.pk, other_family.pk})

    def test_season_filter_switches_away_from_current(self):
        old_season = Season.objects.create(club=self.club, start_date=datetime.date(2020, 8, 1), end_date=datetime.date(2021, 5, 31))
        current_membership = self.make_membership("Current", "Season")
        old_membership = self.make_membership("Old", "Season", season=old_season, fee_status=ClubMembership.FeeStatus.UNPAID)

        response = self.client.get(reverse("management:membership_list") + f"?season={old_season.pk}", HTTP_HOST="ajax-united.rosterchief.app")

        ids = {m.pk for m in response.context["memberships"]}
        self.assertIn(old_membership.pk, ids)
        self.assertNotIn(current_membership.pk, ids)
        # KPIs stay pinned to the *current* season regardless of the list's own filter.
        self.assertEqual(response.context["current_season"], self.season)

    def test_non_admin_gets_403(self):
        coach_user = User.objects.create_user(email="coach-membership@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        team = Team.objects.create(club=self.club, name="U19", short_name="U19")
        position = Position.objects.create(club=self.club, name="Coach8", short_name="C8", staff_position=True)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=position)
        self.client.force_login(coach_user)

        response = self.club_get("membership_list")

        self.assertEqual(response.status_code, 403)

    def test_nav_entry_is_admin_only(self):
        admin_response = self.club_get("home")
        self.assertContains(admin_response, reverse("management:membership_list"))


class MembershipMarkPaidTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)
        self.member = Member.objects.create(first_name="Owed", last_name="Fee")
        self.membership = ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, status=ClubMembership.StatusChoices.PENDING, fee_status=ClubMembership.FeeStatus.UNPAID)

    def test_marking_paid_sets_active_and_paid(self):
        response = self.club_post("membership_mark_paid", {"membership_ids": [str(self.membership.pk)]})

        self.assertRedirects(response, reverse("management:membership_list"))
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, ClubMembership.StatusChoices.ACTIVE)
        self.assertEqual(self.membership.fee_status, ClubMembership.FeeStatus.PAID)
        self.assertEqual(self.membership.activated_at, timezone.localdate())

    def test_marking_paid_grants_the_member_role(self):
        # The whole reason this loops and calls .save() instead of .update() --
        # club/signals.py grants MEMBER via a post_save signal.
        self.assertFalse(ClubRole.objects.filter(club=self.club, member=self.member, role=ClubRole.Roles.MEMBER).exists())

        self.club_post("membership_mark_paid", {"membership_ids": [str(self.membership.pk)]})

        self.assertTrue(ClubRole.objects.filter(club=self.club, member=self.member, role=ClubRole.Roles.MEMBER).exists())

    def test_does_not_overwrite_an_existing_activated_at(self):
        earlier = datetime.date(2026, 1, 1)
        self.membership.activated_at = earlier
        self.membership.save()

        self.club_post("membership_mark_paid", {"membership_ids": [str(self.membership.pk)]})

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.activated_at, earlier)

    def test_does_not_touch_unselected_rows(self):
        other = ClubMembership.objects.create(club=self.club, member=Member.objects.create(first_name="Not", last_name="Selected"), season=self.season, status=ClubMembership.StatusChoices.PENDING, fee_status=ClubMembership.FeeStatus.UNPAID)

        self.club_post("membership_mark_paid", {"membership_ids": [str(self.membership.pk)]})

        other.refresh_from_db()
        self.assertEqual(other.status, ClubMembership.StatusChoices.PENDING)
        self.assertEqual(other.fee_status, ClubMembership.FeeStatus.UNPAID)

    def test_a_membership_from_another_club_is_ignored(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        other_season = make_season(other_club)
        other_member = Member.objects.create(first_name="Other", last_name="Club")
        other_membership = ClubMembership.objects.create(club=other_club, member=other_member, season=other_season, status=ClubMembership.StatusChoices.PENDING, fee_status=ClubMembership.FeeStatus.UNPAID)

        self.club_post("membership_mark_paid", {"membership_ids": [str(other_membership.pk)]})

        other_membership.refresh_from_db()
        self.assertEqual(other_membership.status, ClubMembership.StatusChoices.PENDING)

    def test_redirects_to_a_safe_next(self):
        next_url = reverse("management:membership_list") + "?status=pending"

        response = self.club_post("membership_mark_paid", {"membership_ids": [str(self.membership.pk)], "next": next_url})

        self.assertRedirects(response, next_url)

    def test_ignores_an_unsafe_next(self):
        response = self.club_post("membership_mark_paid", {"membership_ids": [str(self.membership.pk)], "next": "https://evil.example.com/"})

        self.assertRedirects(response, reverse("management:membership_list"))

    def test_nothing_selected_is_a_harmless_no_op(self):
        response = self.club_post("membership_mark_paid", {})

        self.assertRedirects(response, reverse("management:membership_list"))
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, ClubMembership.StatusChoices.PENDING)

    def test_non_admin_gets_403(self):
        coach_user = User.objects.create_user(email="coach-markpaid@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        team = Team.objects.create(club=self.club, name="U20", short_name="U20")
        position = Position.objects.create(club=self.club, name="Coach9", short_name="C9", staff_position=True)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=position)
        self.client.force_login(coach_user)

        response = self.club_post("membership_mark_paid", {"membership_ids": [str(self.membership.pk)]})

        self.assertEqual(response.status_code, 403)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, ClubMembership.StatusChoices.PENDING)


class MembershipRecordPaymentTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)
        self.member = Member.objects.create(first_name="Owed", last_name="Fee")
        self.membership = ClubMembership.objects.create(
            club=self.club, member=self.member, season=self.season, status=ClubMembership.StatusChoices.PENDING, fee_status=ClubMembership.FeeStatus.UNPAID, fee_amount=Decimal("150.00")
        )

    def test_recording_a_partial_payment(self):
        response = self.club_post("membership_record_payment", {"amount": "50.00", "method": FeePayment.Method.CASH, "reference": "R1"}, self.membership.pk)

        self.assertRedirects(response, reverse("management:membership_list"))
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.amount_paid, Decimal("50.00"))
        self.assertEqual(self.membership.fee_status, ClubMembership.FeeStatus.PARTIALLY_PAID)
        payment = FeePayment.objects.get(membership=self.membership)
        self.assertEqual(payment.reference, "R1")
        self.assertEqual(payment.recorded_by, self.admin_user)

    def test_recording_the_full_remaining_amount_settles_it(self):
        self.club_post("membership_record_payment", {"amount": "150.00", "method": FeePayment.Method.BANK_TRANSFER}, self.membership.pk)

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.fee_status, ClubMembership.FeeStatus.PAID)
        self.assertEqual(self.membership.status, ClubMembership.StatusChoices.ACTIVE)

    def test_an_invalid_amount_is_rejected_without_recording_anything(self):
        response = self.club_post("membership_record_payment", {"amount": "0", "method": FeePayment.Method.CASH}, self.membership.pk)

        self.assertRedirects(response, reverse("management:membership_list"))
        self.assertFalse(FeePayment.objects.filter(membership=self.membership).exists())
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.amount_paid, Decimal("0.00"))

    def test_non_admin_gets_403(self):
        coach_user = User.objects.create_user(email="coach-recordpay@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        team = Team.objects.create(club=self.club, name="U22", short_name="U22")
        position = Position.objects.create(club=self.club, name="Coach11", short_name="C11", staff_position=True)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=position)
        self.client.force_login(coach_user)

        response = self.club_post("membership_record_payment", {"amount": "50.00", "method": FeePayment.Method.CASH}, self.membership.pk)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(FeePayment.objects.filter(membership=self.membership).exists())


class MembershipMarkFullyPaidTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)
        self.member = Member.objects.create(first_name="Owed", last_name="Fee")
        self.membership = ClubMembership.objects.create(
            club=self.club, member=self.member, season=self.season, status=ClubMembership.StatusChoices.PENDING, fee_status=ClubMembership.FeeStatus.UNPAID, fee_amount=Decimal("150.00")
        )

    def test_settles_the_remaining_balance_in_one_click(self):
        response = self.club_post("membership_mark_fully_paid", {}, self.membership.pk)

        self.assertRedirects(response, reverse("management:membership_list"))
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.fee_status, ClubMembership.FeeStatus.PAID)
        self.assertEqual(self.membership.status, ClubMembership.StatusChoices.ACTIVE)
        self.assertEqual(self.membership.amount_paid, Decimal("150.00"))

    def test_a_membership_from_another_club_404s(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        other_season = make_season(other_club)
        other_member = Member.objects.create(first_name="Other", last_name="Club")
        other_membership = ClubMembership.objects.create(club=other_club, member=other_member, season=other_season, fee_amount=Decimal("100.00"))

        response = self.club_post("membership_mark_fully_paid", {}, other_membership.pk)

        self.assertEqual(response.status_code, 404)

    def test_redirects_to_a_safe_next(self):
        next_url = reverse("management:membership_list") + "?status=pending"

        response = self.club_post("membership_mark_fully_paid", {"next": next_url}, self.membership.pk)

        self.assertRedirects(response, next_url)

    def test_non_admin_gets_403(self):
        coach_user = User.objects.create_user(email="coach-fullypaid@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        team = Team.objects.create(club=self.club, name="U23", short_name="U23")
        position = Position.objects.create(club=self.club, name="Coach12", short_name="C12", staff_position=True)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=position)
        self.client.force_login(coach_user)

        response = self.club_post("membership_mark_fully_paid", {}, self.membership.pk)

        self.assertEqual(response.status_code, 403)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, ClubMembership.StatusChoices.PENDING)


class MembershipListButtonVisibilityTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)

    def make_membership(self, fee_status):
        member = Member.objects.create(first_name=fee_status, last_name="Row")
        return ClubMembership.objects.create(club=self.club, member=member, season=self.season, fee_status=fee_status, fee_amount=Decimal("100.00"))

    def test_buttons_hidden_for_a_paid_row(self):
        membership = self.make_membership(ClubMembership.FeeStatus.PAID)

        response = self.client.get(reverse("management:membership_list") + "?fee_status=all", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotContains(response, reverse("management:membership_mark_fully_paid", args=[membership.pk]))

    def test_buttons_hidden_for_a_waived_row(self):
        membership = self.make_membership(ClubMembership.FeeStatus.WAIVED)

        response = self.client.get(reverse("management:membership_list") + "?fee_status=all", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertNotContains(response, reverse("management:membership_mark_fully_paid", args=[membership.pk]))

    def test_buttons_shown_for_an_unpaid_row(self):
        membership = self.make_membership(ClubMembership.FeeStatus.UNPAID)

        response = self.client.get(reverse("management:membership_list") + "?fee_status=all", HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, reverse("management:membership_mark_fully_paid", args=[membership.pk]))


class RenderPdfTests(TestCase):
    """management.pdf.render_pdf itself -- see billing/tests.py's equivalent
    coverage of billing.services.invoices.render_pdf, same lazy-import shape."""

    def test_the_pdf_library_is_only_needed_when_a_pdf_is_asked_for(self):
        with mock.patch.dict(sys.modules, {"weasyprint": mock.MagicMock()}):
            sys.modules["weasyprint"].HTML.return_value.write_pdf.return_value = b"%PDF-1.7"

            self.assertEqual(render_pdf("<p>hi</p>"), b"%PDF-1.7")

    def test_a_missing_pdf_library_says_what_is_missing(self):
        with mock.patch.dict(sys.modules, {"weasyprint": None}), self.assertRaises(PDFExportError):
            render_pdf("<p>hi</p>")


class MembershipExportPdfTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)
        self.member = Member.objects.create(first_name="Print", last_name="Me")
        ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE, fee_status=ClubMembership.FeeStatus.UNPAID)

    def test_downloads_as_a_pdf(self):
        with mock.patch("management.views.membership_list_pdf", return_value=b"%PDF-fake") as renderer:
            response = self.club_get("membership_export_pdf")

        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(".pdf", response["Content-Disposition"])
        self.assertEqual(response.content, b"%PDF-fake")
        renderer.assert_called_once()

    def test_export_uses_the_same_filters_as_the_page(self):
        # Same fixture as the "on-team" filter test for the page itself -- the PDF
        # must reflect whatever's filtered, not the whole club.
        other = Member.objects.create(first_name="Other", last_name="Person")
        ClubMembership.objects.create(club=self.club, member=other, season=self.season, status=ClubMembership.StatusChoices.ACTIVE, fee_status=ClubMembership.FeeStatus.UNPAID)

        with mock.patch("management.views.membership_list_pdf", return_value=b"%PDF-fake") as renderer:
            self.client.get(reverse("management:membership_export_pdf") + "?q=Print", HTTP_HOST="ajax-united.rosterchief.app")

        context = renderer.call_args[0][0]
        names = {m.member.last_name for m in context["memberships"]}
        self.assertEqual(names, {"Me"})

    def test_a_missing_pdf_library_is_reported_rather_than_a_500(self):
        # WeasyPrint needs native libs. Without them the button must explain itself.
        with mock.patch("management.views.membership_list_pdf", side_effect=PDFExportError("PDF rendering needs the native pango/cairo libraries.")):
            response = self.club_get("membership_export_pdf")
        response = self.client.get(response.url, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertContains(response, "pango")

    def test_non_admin_gets_403(self):
        coach_user = User.objects.create_user(email="coach-export@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        team = Team.objects.create(club=self.club, name="U21", short_name="U21")
        position = Position.objects.create(club=self.club, name="Coach10", short_name="C10", staff_position=True)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=position)
        self.client.force_login(coach_user)

        response = self.club_get("membership_export_pdf")

        self.assertEqual(response.status_code, 403)


class MemberListRowActionsTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.member = Member.objects.create(first_name="Row", last_name="Actions")
        ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)

    def test_admin_sees_edit_and_delete_buttons(self):
        self.client.force_login(self.admin_user)

        response = self.club_get("member_list")

        self.assertContains(response, reverse("management:member_delete", args=[self.member.pk]))

    def test_non_admin_does_not_see_edit_and_delete_buttons(self):
        # Roster self.member on the coach's team so this actually tests the
        # admin-only button gating, not just "the coach can't see this row at all".
        coach_user = User.objects.create_user(email="coach5@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        team = Team.objects.create(club=self.club, name="U16", short_name="U16")
        staff_position = Position.objects.create(club=self.club, name="Coach5", short_name="C5", staff_position=True, management_position=True)
        player_position = Position.objects.create(club=self.club, name="Player5", short_name="P5", staff_position=False)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=staff_position)
        TeamMembership.objects.create(team=team, member=self.member, season=self.season, position=player_position)
        self.client.force_login(coach_user)

        response = self.club_get("member_list")

        self.assertContains(response, "Row")
        self.assertNotContains(response, reverse("management:member_delete", args=[self.member.pk]))


class HomeViewTests(ManagementTestBase):
    """The dashboard reuses controlpanel.services.statistics' club_attention/
    club_charts/club_statistics -- already club-scoped, so directly usable for this
    club's own staff. Financial pieces (fee chart, Shop stat group) are admin-only,
    same line the nav already draws around the Shop section."""

    def make_coach(self, email):
        coach_user = User.objects.create_user(email=email, password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        team = Team.objects.create(club=self.club, name=f"Team-{email}", short_name="T")
        position = Position.objects.create(club=self.club, name=f"Coach-{email}", short_name="C", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=position)
        return coach_user

    def test_admin_sees_the_financial_sections(self):
        self.client.force_login(self.admin_user)

        response = self.club_get("home")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="fees-chart"')
        self.assertContains(response, "Open carts")

    def test_non_admin_staff_does_not_see_the_financial_sections(self):
        self.client.force_login(self.make_coach("coach6@example.com"))

        response = self.club_get("home")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="fees-chart"')
        self.assertNotContains(response, "Open carts")

    def test_upcoming_events_are_listed_in_order_and_future_only(self):
        now = timezone.now()
        past = Event.objects.create(club=self.club, kind=Event.EventKind.TRAINING, title="Past training", start=now - datetime.timedelta(days=1))
        soon = Event.objects.create(club=self.club, kind=Event.EventKind.TRAINING, title="Sooner training", start=now + datetime.timedelta(days=1))
        later = Event.objects.create(club=self.club, kind=Event.EventKind.MATCH, title="Later match", start=now + datetime.timedelta(days=5))
        self.client.force_login(self.admin_user)

        response = self.club_get("home")

        body = response.content.decode()
        self.assertNotIn(past.title, body)
        self.assertLess(body.index(soon.title), body.index(later.title))

    def test_loads_fine_with_no_season_and_no_events(self):
        # A fresh club, never given a season -- ClubMembership.season is PROTECT, so
        # this is a new club rather than deleting self.season out from under setUp's
        # own ClubMembership.
        empty_club = Club.objects.create(name="Empty FC", slug="empty-fc")
        admin_member = Member.objects.create(first_name="Empty", last_name="Admin")
        ClubRole.objects.create(club=empty_club, member=admin_member, role=ClubRole.Roles.ADMIN)
        admin_user = User.objects.create_user(email="emptyadmin@example.com", password="pw-secret-123")
        admin_member.user = admin_user
        admin_member.save()
        enrol_mfa(admin_user)
        self.client.force_login(admin_user)

        with override_settings(ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "empty-fc.rosterchief.app", "testserver"]):
            response = self.client.get(reverse("management:home"), HTTP_HOST="empty-fc.rosterchief.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot take a signup")
        self.assertContains(response, "Nothing scheduled.")


class MemberBulkImportTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)

    def make_non_admin_staff(self):
        coach_user = User.objects.create_user(email="coach-import@example.com", password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        team = Team.objects.create(club=self.club, name="U14", short_name="U14")
        position = Position.objects.create(club=self.club, name="CoachImport", short_name="CI", staff_position=True)
        StaffAssignment.objects.create(team=team, member=coach_member, season=self.season, position=position)
        return coach_user

    def test_template_download_has_expected_headers_and_dropdowns(self):
        response = self.club_get("member_import_template")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], XLSX_CONTENT_TYPE)

        workbook = openpyxl.load_workbook(BytesIO(response.content))
        sheet = workbook.active
        header = [cell.value for cell in sheet[1]]
        self.assertEqual(header, TEMPLATE_COLUMNS)
        self.assertTrue(sheet.data_validations.dataValidation)

    def test_non_admin_can_download_template_but_not_upload(self):
        coach_user = self.make_non_admin_staff()
        self.client.force_login(coach_user)

        template_response = self.club_get("member_import_template")
        upload_response = self.club_post("member_import", {"file": make_import_workbook([["Jamie", "Kid", "", "jamie@example.com", "", "", "", "", ""]])})

        self.assertEqual(template_response.status_code, 200)
        self.assertEqual(upload_response.status_code, 403)

    def test_anonymous_is_redirected_to_login_for_both(self):
        self.client.logout()

        self.assertEqual(self.club_get("member_import_template").status_code, 302)
        self.assertEqual(self.club_get("member_import").status_code, 302)

    def test_a_clean_row_previews_as_will_create(self):
        upload = make_import_workbook([["Jamie", "Kid", "2010-01-01", "jamie.kid@example.com", "+32470111111", "", "", "", ""]])

        response = self.club_post("member_import", {"file": upload})

        self.assertEqual(response.context["valid_count"], 1)
        self.assertEqual(response.context["skipped_count"], 0)
        result = response.context["results"][0]
        self.assertIsNotNone(result["member"])
        self.assertEqual(result["membership_kwargs"]["status"], ClubMembership.StatusChoices.ACTIVE)
        self.assertEqual(result["membership_kwargs"]["fee_status"], ClubMembership.FeeStatus.UNPAID)

    def test_a_row_missing_a_required_name_is_skipped(self):
        upload = make_import_workbook([["", "Noname", "", "", "", "", "", "", ""]])

        response = self.club_post("member_import", {"file": upload})

        self.assertEqual(response.context["valid_count"], 0)
        result = response.context["results"][0]
        self.assertIsNone(result["member"])
        self.assertTrue(result["errors"])

    def test_an_invalid_email_is_skipped(self):
        upload = make_import_workbook([["Bad", "Email", "", "not-an-email", "", "", "", "", ""]])

        response = self.club_post("member_import", {"file": upload})

        result = response.context["results"][0]
        self.assertIsNone(result["member"])

    def test_an_invalid_status_value_is_skipped(self):
        upload = make_import_workbook([["Bad", "Status", "", "bad.status@example.com", "", "", "", "not-a-status", ""]])

        response = self.club_post("member_import", {"file": upload})

        result = response.context["results"][0]
        self.assertIsNone(result["member"])
        self.assertTrue(any("status" in error.lower() for error in result["errors"]))

    def test_a_duplicate_email_within_the_file_is_flagged_on_the_second_row(self):
        upload = make_import_workbook(
            [
                ["First", "Dup", "", "dup@example.com", "", "", "", "", ""],
                ["Second", "Dup", "", "dup@example.com", "", "", "", "", ""],
            ]
        )

        response = self.club_post("member_import", {"file": upload})

        results = response.context["results"]
        self.assertIsNotNone(results[0]["member"])
        self.assertIsNone(results[1]["member"])
        self.assertIn("Duplicate email in this file.", results[1]["errors"])

    def test_an_email_already_in_the_club_is_skipped(self):
        upload = make_import_workbook([["Ada", "Admin", "", self.admin_user.email, "", "", "", "", ""]])

        response = self.club_post("member_import", {"file": upload})

        result = response.context["results"][0]
        self.assertIsNone(result["member"])
        self.assertIn("Already a member of this club.", result["errors"])

    def test_confirm_creates_only_the_valid_rows_with_defaults(self):
        upload = make_import_workbook(
            [
                ["Jamie", "Kid", "2010-01-01", "jamie.kid@example.com", "", "", "LIC-1", "", ""],
                ["", "Noname", "", "", "", "", "", "", ""],
            ]
        )
        self.club_post("member_import", {"file": upload})

        response = self.club_post("member_import_confirm", {})

        self.assertRedirects(response, reverse("management:member_list"))
        member = Member.objects.get(email="jamie.kid@example.com")
        membership = ClubMembership.objects.get(club=self.club, member=member)
        self.assertEqual(membership.status, ClubMembership.StatusChoices.ACTIVE)
        self.assertEqual(membership.fee_status, ClubMembership.FeeStatus.UNPAID)
        self.assertEqual(membership.license, "LIC-1")
        self.assertFalse(Member.objects.filter(last_name="Noname").exists())

    def test_confirm_without_a_prior_upload_creates_nothing(self):
        response = self.club_post("member_import_confirm", {})

        self.assertRedirects(response, reverse("management:member_import"))
        self.assertEqual(Member.objects.filter(last_name="Kid").count(), 0)

    def test_confirm_is_admin_only(self):
        coach_user = self.make_non_admin_staff()
        upload = make_import_workbook([["Jamie", "Kid", "", "jamie.kid2@example.com", "", "", "", "", ""]])
        self.club_post("member_import", {"file": upload})

        self.client.force_login(coach_user)
        response = self.club_post("member_import_confirm", {})

        self.assertEqual(response.status_code, 403)


class NewsManagementTests(ManagementTestBase):
    def setUp(self):
        super().setUp()
        self.team = Team.objects.create(club=self.club, name="First Team", short_name="1st")

    def make_coach_manager(self, email="coach-news@example.com"):
        coach_user = User.objects.create_user(email=email, password="pw-secret-123")
        coach_member = Member.objects.create(user=coach_user, first_name="Cara", last_name="Coach")
        position = Position.objects.create(club=self.club, name="Head Coach", short_name="HC", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=self.team, member=coach_member, season=self.season, position=position)
        return coach_user

    def make_plain_staff(self, email="physio-news@example.com"):
        staff_user = User.objects.create_user(email=email, password="pw-secret-123")
        staff_member = Member.objects.create(user=staff_user, first_name="Pat", last_name="Physio")
        position = Position.objects.create(club=self.club, name="Physio", short_name="PH", staff_position=True, management_position=False)
        StaffAssignment.objects.create(team=self.team, member=staff_member, season=self.season, position=position)
        return staff_user

    def make_editor(self, email="editor-news@example.com"):
        editor_user = User.objects.create_user(email=email, password="pw-secret-123")
        editor_member = Member.objects.create(user=editor_user, first_name="Eve", last_name="Editor")
        ClubMembership.objects.create(club=self.club, member=editor_member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        ClubRole.objects.filter(club=self.club, member=editor_member).update(role=ClubRole.Roles.EDITOR)
        enrol_mfa(editor_user)  # ClubRole ADMIN/EDITOR requires a second factor; StaffAssignment-only doesn't.
        return editor_user

    def test_list_is_scoped_to_the_club(self):
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        News.objects.create(club=other_club, title="Rival news", body="Body.")
        self.client.force_login(self.admin_user)

        response = self.club_get("news_list")

        self.assertNotContains(response, "Rival news")

    def test_a_coach_manager_can_create_a_draft(self):
        self.client.force_login(self.make_coach_manager())

        response = self.club_post("news_create", {"title": "Season Kickoff", "body": "Big news.", "visibility": News.Visibility.INTERNAL, "teams": [str(self.team.pk)]})

        item = News.objects.get(club=self.club, title="Season Kickoff")
        self.assertRedirects(response, reverse("management:news_detail", args=[item.pk]))
        self.assertEqual(item.status, News.Status.DRAFT)

    def test_plain_staff_cannot_create_news(self):
        self.client.force_login(self.make_plain_staff())

        response = self.club_post("news_create", {"title": "Not allowed", "body": "Body.", "visibility": News.Visibility.INTERNAL})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(News.objects.filter(club=self.club, title="Not allowed").exists())

    def test_coach_manager_cannot_publish(self):
        item = News.objects.create(club=self.club, title="Draft item", body="Body.")
        self.client.force_login(self.make_coach_manager())

        response = self.club_post("news_publish", {"published_at": "2026-08-10T10:00"}, item.pk)

        self.assertEqual(response.status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.status, News.Status.DRAFT)

    def test_editor_can_publish(self):
        item = News.objects.create(club=self.club, title="Draft item", body="Body.")
        self.client.force_login(self.make_editor())

        self.club_post("news_publish", {"published_at": "2026-08-10T10:00"}, item.pk)

        item.refresh_from_db()
        self.assertEqual(item.status, News.Status.PUBLISHED)

    def test_publishing_with_a_future_date_leaves_it_scheduled(self):
        item = News.objects.create(club=self.club, title="Draft item", body="Body.")
        self.client.force_login(self.make_editor())
        future = timezone.now() + datetime.timedelta(days=7)

        self.club_post("news_publish", {"published_at": future.strftime("%Y-%m-%dT%H:%M")}, item.pk)

        item.refresh_from_db()
        self.assertTrue(item.is_scheduled)

    def test_publishing_with_now_makes_it_live_immediately(self):
        item = News.objects.create(club=self.club, title="Draft item", body="Body.")
        self.client.force_login(self.make_editor())

        self.club_post("news_publish", {"published_at": timezone.now().strftime("%Y-%m-%dT%H:%M")}, item.pk)

        item.refresh_from_db()
        self.assertFalse(item.is_scheduled)

    def test_unpublishing_reverts_to_draft(self):
        item = News.objects.create(club=self.club, title="Live item", body="Body.")
        item.publish()
        self.client.force_login(self.make_editor())

        self.club_post("news_unpublish", {}, item.pk)

        item.refresh_from_db()
        self.assertEqual(item.status, News.Status.DRAFT)
        self.assertIsNone(item.published_at)

    def test_a_coach_manager_can_edit_someone_elses_draft(self):
        item = News.objects.create(club=self.club, title="Old title", body="Body.")
        self.client.force_login(self.make_coach_manager())

        self.club_post("news_update", {"title": "New title", "body": "Body.", "visibility": News.Visibility.INTERNAL}, item.pk)

        item.refresh_from_db()
        self.assertEqual(item.title, "New title")

    def test_a_coach_manager_cannot_edit_once_published(self):
        item = News.objects.create(club=self.club, title="Old title", body="Body.")
        item.publish()
        self.client.force_login(self.make_coach_manager())

        response = self.club_post("news_update", {"title": "New title", "body": "Body.", "visibility": News.Visibility.INTERNAL}, item.pk)

        self.assertEqual(response.status_code, 403)

    def test_an_editor_can_still_edit_once_published(self):
        item = News.objects.create(club=self.club, title="Old title", body="Body.")
        item.publish()
        self.client.force_login(self.make_editor())

        self.club_post("news_update", {"title": "New title", "body": "Body.", "visibility": News.Visibility.INTERNAL}, item.pk)

        item.refresh_from_db()
        self.assertEqual(item.title, "New title")

    def test_uploading_multiple_photos_creates_one_per_file_and_marks_the_first_main(self):
        item = News.objects.create(club=self.club, title="Match report", body="Body.")
        self.client.force_login(self.make_coach_manager())
        images = [
            SimpleUploadedFile("one.jpg", b"fake-bytes-one", content_type="image/jpeg"),
            SimpleUploadedFile("two.jpg", b"fake-bytes-two", content_type="image/jpeg"),
        ]

        self.club_post("news_photo_upload", {"images": images}, item.pk)

        self.assertEqual(item.photos.count(), 2)
        self.assertEqual(item.photos.filter(is_main=True).count(), 1)

    def test_set_main_moves_the_main_flag(self):
        item = News.objects.create(club=self.club, title="Match report", body="Body.")
        first = NewsPhoto.objects.create(news_item=item, image=SimpleUploadedFile("one.jpg", b"one", content_type="image/jpeg"), is_main=True)
        second = NewsPhoto.objects.create(news_item=item, image=SimpleUploadedFile("two.jpg", b"two", content_type="image/jpeg"), is_main=False)
        self.client.force_login(self.make_coach_manager())

        self.club_post("news_photo_set_main", {}, item.pk, second.pk)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_main)
        self.assertTrue(second.is_main)

    def test_deleting_a_photo_removes_it(self):
        item = News.objects.create(club=self.club, title="Match report", body="Body.")
        photo = NewsPhoto.objects.create(news_item=item, image=SimpleUploadedFile("one.jpg", b"one", content_type="image/jpeg"))
        self.client.force_login(self.make_coach_manager())

        self.club_post("news_photo_delete", {}, item.pk, photo.pk)

        self.assertFalse(NewsPhoto.objects.filter(pk=photo.pk).exists())

    def test_a_coach_manager_can_delete_a_draft(self):
        item = News.objects.create(club=self.club, title="Draft item", body="Body.")
        self.client.force_login(self.make_coach_manager())

        response = self.club_post("news_delete", {}, item.pk)

        self.assertRedirects(response, reverse("management:news_list"))
        self.assertFalse(News.objects.filter(pk=item.pk).exists())

    def test_a_coach_manager_cannot_delete_once_published(self):
        item = News.objects.create(club=self.club, title="Live item", body="Body.")
        item.publish()
        self.client.force_login(self.make_coach_manager())

        response = self.club_post("news_delete", {}, item.pk)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(News.objects.filter(pk=item.pk).exists())

    def test_an_editor_can_delete_once_published(self):
        item = News.objects.create(club=self.club, title="Live item", body="Body.")
        item.publish()
        self.client.force_login(self.make_editor())

        self.club_post("news_delete", {}, item.pk)

        self.assertFalse(News.objects.filter(pk=item.pk).exists())

    def test_deleting_a_news_item_removes_its_photos(self):
        item = News.objects.create(club=self.club, title="Match report", body="Body.")
        photo = NewsPhoto.objects.create(news_item=item, image=SimpleUploadedFile("one.jpg", b"one", content_type="image/jpeg"))
        self.client.force_login(self.make_coach_manager())

        self.club_post("news_delete", {}, item.pk)

        self.assertFalse(NewsPhoto.objects.filter(pk=photo.pk).exists())

    def test_the_edit_and_delete_buttons_are_hidden_once_published_for_a_coach_manager(self):
        item = News.objects.create(club=self.club, title="Live item", body="Body.")
        item.publish()
        self.client.force_login(self.make_coach_manager())

        response = self.club_get("news_list")

        self.assertNotContains(response, reverse("management:news_update", args=[item.pk]))
