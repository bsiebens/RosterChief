from decimal import Decimal

from allauth.mfa.models import Authenticator
from django import forms
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from club.models import Club, ClubMembership, ClubRole, Season
from members.models import Member
from shop.models import Order
from teams.models import Position, Team, TeamMembership

from .services.admins import grant_club_admin
from .services.statistics import club_statistics, clubs_with_totals, platform_totals
from .templatetags.ui import daisy

User = get_user_model()


def enrol_mfa(user):
    return Authenticator.objects.create(user=user, type=Authenticator.Type.TOTP, data={"secret": "JBSWY3DPEHPK3PXP"})


class ControlPanelTestBase(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United")
        self.staff = User.objects.create_user(email="root@example.com", password="pw-secret-123", is_staff=True)
        # Staff must hold a second factor, else RequireMFAMiddleware redirects.
        enrol_mfa(self.staff)
        self.client.force_login(self.staff)


class AccessTests(ControlPanelTestBase):
    def test_staff_can_reach_the_panel(self):
        self.assertEqual(self.client.get(reverse("controlpanel:dashboard")).status_code, 200)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()

        response = self.client.get(reverse("controlpanel:dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response.url)

    def test_signed_in_non_staff_gets_403(self):
        self.client.force_login(User.objects.create_user(email="member@example.com", password="pw-secret-123"))

        self.assertEqual(self.client.get(reverse("controlpanel:dashboard")).status_code, 403)

    def test_superuser_can_reach_the_panel(self):
        root = User.objects.create_superuser(email="super@example.com", password="pw-secret-123")
        enrol_mfa(root)
        self.client.force_login(root)

        self.assertEqual(self.client.get(reverse("controlpanel:dashboard")).status_code, 200)

    @override_settings(CLUBMANAGER_BASE_DOMAIN="clubmanager.app", ALLOWED_HOSTS=[".clubmanager.app"])
    def test_panel_does_not_exist_on_a_club_subdomain(self):
        # It manages *all* clubs, so it must not be reachable from inside one.
        Club.objects.create(name="Rival FC", slug="rival-fc")

        response = self.client.get(reverse("controlpanel:dashboard"), headers={"host": "rival-fc.clubmanager.app"})

        self.assertEqual(response.status_code, 404)

    def test_staff_without_a_second_factor_is_sent_to_enrolment(self):
        self.client.force_login(User.objects.create_user(email="nomfa@example.com", password="pw-secret-123", is_staff=True))

        response = self.client.get(reverse("controlpanel:dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("mfa_index"))


class ClubManagementTests(ControlPanelTestBase):
    def test_dashboard_lists_clubs(self):
        self.assertContains(self.client.get(reverse("controlpanel:dashboard")), "Ajax United")

    def test_create_club_derives_the_slug(self):
        response = self.client.post(reverse("controlpanel:club_create"), {"name": "New Club", "slug": ""})

        club = Club.objects.get(name="New Club")
        self.assertEqual(club.slug, "new-club")
        self.assertRedirects(response, reverse("controlpanel:club_detail", args=[club.pk]))

    def test_update_club(self):
        self.client.post(reverse("controlpanel:club_update", args=[self.club.pk]), {"name": "Renamed", "slug": self.club.slug})

        self.club.refresh_from_db()
        self.assertEqual(self.club.name, "Renamed")

    def test_club_detail_shows_statistics(self):
        response = self.client.get(reverse("controlpanel:club_detail", args=[self.club.pk]))

        self.assertContains(response, "Members")
        self.assertContains(response, "Teams &amp; staff")
        self.assertContains(response, "Shop")

    def test_archive_then_restore(self):
        self.client.post(reverse("controlpanel:club_archive", args=[self.club.pk]))
        self.club.refresh_from_db()
        self.assertTrue(self.club.is_archived)

        self.client.post(reverse("controlpanel:club_restore", args=[self.club.pk]))
        self.club.refresh_from_db()
        self.assertFalse(self.club.is_archived)

    def test_list_separates_active_from_archived(self):
        Club.objects.create(name="Gone FC").archive()

        active = self.client.get(reverse("controlpanel:club_list"))
        self.assertContains(active, "Ajax United")
        self.assertNotContains(active, "Gone FC")

        archived = self.client.get(reverse("controlpanel:club_list"), {"archived": "1"})
        self.assertContains(archived, "Gone FC")
        self.assertNotContains(archived, "Ajax United")

    def test_list_search(self):
        Club.objects.create(name="Rival FC")

        response = self.client.get(reverse("controlpanel:club_list"), {"q": "Ajax"})

        self.assertContains(response, "Ajax United")
        self.assertNotContains(response, "Rival FC")


class ClubAdminManagementTests(ControlPanelTestBase):
    def add_admin(self, **data):
        return self.client.post(reverse("controlpanel:club_admin_add", args=[self.club.pk]), data)

    def test_granting_admin_to_a_new_email_creates_the_account(self):
        self.add_admin(email="New.Admin@Example.com", first_name="Ada", last_name="Min")

        user = User.objects.get(email="new.admin@example.com")
        self.assertFalse(user.has_usable_password())  # they set one via password reset
        self.assertEqual(ClubRole.objects.get(club=self.club, member__user=user).role, ClubRole.Roles.ADMIN)

    def test_a_new_email_must_come_with_a_name(self):
        response = self.add_admin(email="nameless@example.com", first_name="", last_name="")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ClubRole.objects.exists())
        self.assertFormError(response.context["form"], "first_name", "Required: this email has no account yet.")

    def test_an_existing_member_is_promoted_rather_than_duplicated(self):
        user = User.objects.create_user(email="existing@example.com", password="pw-secret-123")
        member = Member.objects.create(user=user, first_name="Ex", last_name="Isting")
        ClubRole.objects.create(club=self.club, member=member, role=ClubRole.Roles.MEMBER)

        self.add_admin(email="existing@example.com")

        # One role per member per club, so the MEMBER role is upgraded in place.
        self.assertEqual(ClubRole.objects.get(club=self.club, member=member).role, ClubRole.Roles.ADMIN)
        self.assertEqual(Member.objects.filter(user=user).count(), 1)

    def test_granting_twice_is_idempotent(self):
        self.add_admin(email="ada@example.com", first_name="Ada", last_name="Min")
        self.add_admin(email="ada@example.com", first_name="Ada", last_name="Min")

        self.assertEqual(ClubRole.objects.filter(club=self.club).count(), 1)

    def test_remove_admin(self):
        role = grant_club_admin(self.club, "ada@example.com", "Ada", "Min")

        self.client.post(reverse("controlpanel:club_admin_remove", args=[self.club.pk, role.pk]))

        self.assertFalse(ClubRole.objects.filter(pk=role.pk).exists())

    def test_admins_are_listed_on_the_club(self):
        grant_club_admin(self.club, "ada@example.com", "Ada", "Min")

        response = self.client.get(reverse("controlpanel:club_detail", args=[self.club.pk]))

        self.assertContains(response, "Ada Min")
        self.assertContains(response, "ada@example.com")


class StatisticsTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United")
        today = timezone.localdate()
        self.season = Season.objects.create(club=self.club, start_date=today, end_date=today)
        self.member = Member.objects.create(first_name="Jane", last_name="Doe")

    def groups_for(self, club):
        return {group["title"]: dict(group["stats"]) for group in club_statistics(club)}

    def test_platform_totals_split_active_and_archived(self):
        Club.objects.create(name="Gone FC").archive()

        totals = platform_totals()

        self.assertEqual(totals["clubs"], 1)
        self.assertEqual(totals["archived_clubs"], 1)

    def test_club_totals_are_annotated(self):
        Team.objects.create(club=self.club, name="First", short_name="1st")
        ClubMembership.objects.create(club=self.club, member=self.member, season=self.season)

        club = clubs_with_totals().get(pk=self.club.pk)

        self.assertEqual(club.member_count, 1)
        self.assertEqual(club.team_count, 1)
        self.assertEqual(club.admin_count, 0)

    def test_club_statistics_count_members_teams_and_money(self):
        ClubMembership.objects.create(club=self.club, member=self.member, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        team = Team.objects.create(club=self.club, name="First", short_name="1st")
        position = Position.objects.create(club=self.club, name="Forward", short_name="FW")
        TeamMembership.objects.create(team=team, member=self.member, season=self.season, position=position)
        Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("50.00"), status=Order.OrderStatus.PAID)
        Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("20.00"), status=Order.OrderStatus.PENDING)

        groups = self.groups_for(self.club)

        self.assertEqual(groups["Members"]["Active this season"], 1)
        self.assertEqual(groups["Teams & staff"]["Players this season"], 1)
        self.assertEqual(groups["Shop"]["Revenue"], Decimal("50.00"))
        self.assertEqual(groups["Shop"]["Outstanding"], Decimal("20.00"))

    def test_statistics_cope_with_no_current_season(self):
        # A brand-new club has no season covering today; it must not blow up.
        groups = self.groups_for(Club.objects.create(name="Seasonless FC"))

        self.assertEqual(groups["Members"]["Active this season"], 0)
        self.assertEqual(groups["Teams & staff"]["Players this season"], 0)
        self.assertEqual(groups["Events"]["This season"], 0)
        self.assertEqual(groups["Shop"]["Revenue"], Decimal("0.00"))


class SampleForm(forms.Form):
    text = forms.CharField()
    choice = forms.ChoiceField(choices=[("a", "A")])
    note = forms.CharField(widget=forms.Textarea)
    agree = forms.BooleanField()


class DaisyFilterTests(TestCase):
    def rendered(self, field_name, data=None):
        form = SampleForm(data)
        if data is not None:
            form.is_valid()
        return str(daisy(form[field_name]))

    def test_widgets_get_the_right_daisyui_class(self):
        self.assertIn("input input-bordered", self.rendered("text"))
        self.assertIn("select select-bordered", self.rendered("choice"))
        self.assertIn("textarea textarea-bordered", self.rendered("note"))
        self.assertIn("checkbox", self.rendered("agree"))

    def test_invalid_fields_get_an_error_class(self):
        self.assertIn("input-error", self.rendered("text", data={}))
