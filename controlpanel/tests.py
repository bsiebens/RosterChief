import datetime
import pathlib
from decimal import Decimal
from unittest import mock

from allauth.mfa.models import Authenticator
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.base import Message
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from waffle import get_waffle_flag_model, get_waffle_switch_model

from billing.models import GRACE_DAYS, Due, Tier, TierPrice
from billing.services import BillingError
from billing.services.dues import record_payment, subscribe, waive
from club.models import Club, ClubMembership, ClubRole, Season
from events.models import Attendance, Event
from features.models import Maintenance
from members.models import Member
from shop.models import Order
from teams.models import Position, StaffAssignment, Team, TeamMembership

from .services.admins import grant_club_admin
from .services.platform_admins import PlatformAdminError, is_last_superuser, set_platform_access
from .services.statistics import (
    admins_pending_mfa,
    attendance_rates,
    club_attention,
    club_statistics,
    clubs_with_health,
    clubs_with_totals,
    clubs_without_a_season,
    dormant_clubs,
    fee_aging,
    flag_adoption,
    new_members,
    onboarding_funnel,
    platform_attention,
    platform_charts,
    platform_totals,
    renewal_rate,
    signup_split,
    teams_without_a_manager,
    unrostered_members,
)
from .templatetags.ui import as_alert, daisy, excluded, field_icon

User = get_user_model()
Flag = get_waffle_flag_model()
Switch = get_waffle_switch_model()


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

    @override_settings(ROSTERCHIEF_BASE_DOMAIN="rosterchief.app", ALLOWED_HOSTS=[".rosterchief.app"])
    def test_panel_does_not_exist_on_a_club_subdomain(self):
        # It manages *all* clubs, so it must not be reachable from inside one.
        Club.objects.create(name="Rival FC", slug="rival-fc")

        response = self.client.get(reverse("controlpanel:dashboard"), headers={"host": "rival-fc.rosterchief.app"})

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


class PlatformAdminAccessTests(ControlPanelTestBase):
    """Managing platform admins is superuser-only: the panel is gated on
    is_staff OR is_superuser, so letting staff grant superuser would collapse
    the two into one privilege level."""

    def test_staff_cannot_reach_the_admins_section(self):
        self.assertEqual(self.client.get(reverse("controlpanel:admins")).status_code, 403)

    def test_staff_cannot_grant_platform_access(self):
        self.assertEqual(self.client.get(reverse("controlpanel:admin_add")).status_code, 403)

    def test_the_admins_tab_is_hidden_from_staff(self):
        self.assertNotContains(self.client.get(reverse("controlpanel:dashboard")), reverse("controlpanel:admins"))


class PlatformAdminTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser(email="root@example.com", password="pw-secret-123")
        enrol_mfa(self.root)
        self.client.force_login(self.root)

    def test_superuser_sees_the_admins_section(self):
        self.assertEqual(self.client.get(reverse("controlpanel:admins")).status_code, 200)

    def test_the_grant_form_renders(self):
        self.assertEqual(self.client.get(reverse("controlpanel:admin_add")).status_code, 200)

    def test_grant_access_to_a_new_email_creates_a_staff_account(self):
        self.client.post(reverse("controlpanel:admin_add"), {"email": "New.Admin@Example.com"})

        user = User.objects.get(email="new.admin@example.com")
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_usable_password())  # set via password reset

    def test_grant_superuser(self):
        self.client.post(reverse("controlpanel:admin_add"), {"email": "boss@example.com", "is_superuser": "1"})

        user = User.objects.get(email="boss@example.com")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)  # superuser implies staff, else they can't reach the panel

    def test_promote_and_demote_another_admin(self):
        other = User.objects.create_user(email="other@example.com", password="pw-secret-123", is_staff=True)

        self.client.post(reverse("controlpanel:admin_update", args=[other.pk]), {"is_staff": "1", "is_superuser": "1"})
        other.refresh_from_db()
        self.assertTrue(other.is_superuser)

        self.client.post(reverse("controlpanel:admin_update", args=[other.pk]), {"is_staff": "1", "is_superuser": "0"})
        other.refresh_from_db()
        self.assertFalse(other.is_superuser)

    def test_revoke_another_admins_access(self):
        other = User.objects.create_user(email="other@example.com", password="pw-secret-123", is_staff=True)

        self.client.post(reverse("controlpanel:admin_revoke", args=[other.pk]))

        other.refresh_from_db()
        self.assertFalse(other.is_staff)
        self.assertFalse(other.is_superuser)

    # --- guardrails: it must be impossible to lock the platform out of itself ---
    def test_cannot_revoke_your_own_access(self):
        response = self.client.post(reverse("controlpanel:admin_revoke", args=[self.root.pk]), follow=True)

        self.root.refresh_from_db()
        self.assertTrue(self.root.is_superuser)
        self.assertContains(response, "cannot remove your own platform access")

    def test_cannot_remove_your_own_superuser_rights(self):
        # Keep another superuser around so this is blocked by the self-rule, not
        # by the last-superuser rule.
        User.objects.create_superuser(email="spare@example.com", password="pw-secret-123")

        response = self.client.post(reverse("controlpanel:admin_update", args=[self.root.pk]), {"is_staff": "1", "is_superuser": "0"}, follow=True)

        self.root.refresh_from_db()
        self.assertTrue(self.root.is_superuser)
        self.assertContains(response, "cannot remove your own superuser rights")

    def test_the_last_superuser_cannot_be_demoted(self):
        other = User.objects.create_superuser(email="other@example.com", password="pw-secret-123")
        # Now demote self is blocked by the self-rule; demote `other` is fine...
        self.client.post(reverse("controlpanel:admin_update", args=[other.pk]), {"is_staff": "1", "is_superuser": "0"})
        other.refresh_from_db()
        self.assertFalse(other.is_superuser)

        # ...leaving root as the last superuser, who now cannot be demoted by anyone.
        self.assertTrue(is_last_superuser(self.root))
        with self.assertRaises(PlatformAdminError):
            set_platform_access(other, self.root, is_staff=True, is_superuser=False)

    def test_last_superuser_rule_is_enforced_for_other_actors_too(self):
        response = self.client.post(reverse("controlpanel:admin_revoke", args=[self.root.pk]), follow=True)

        self.root.refresh_from_db()
        self.assertTrue(self.root.is_superuser)
        self.assertContains(response, "cannot remove your own platform access")


class FeatureViewTests(ControlPanelTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.flag = Flag.objects.create(name="shop")

    def test_features_page_lists_flags_and_switches(self):
        Switch.objects.create(name="maintenance", active=False)

        response = self.client.get(reverse("controlpanel:features"))

        self.assertContains(response, "shop")
        self.assertContains(response, "maintenance")

    def test_the_flag_forms_render(self):
        self.assertEqual(self.client.get(reverse("controlpanel:flag_create")).status_code, 200)
        self.assertEqual(self.client.get(reverse("controlpanel:flag_update", args=[self.flag.pk])).status_code, 200)

    def test_create_a_flag(self):
        self.client.post(reverse("controlpanel:flag_create"), {"name": "news", "note": "News module", "percent": "", "everyone": ""})

        self.assertTrue(Flag.objects.filter(name="news").exists())

    def test_edit_a_flag(self):
        self.client.post(reverse("controlpanel:flag_update", args=[self.flag.pk]), {"name": "shop", "note": "Webshop", "percent": "", "everyone": ""})

        self.flag.refresh_from_db()
        self.assertEqual(self.flag.note, "Webshop")

    def test_toggle_a_feature_on_and_off_for_a_club(self):
        url = reverse("controlpanel:club_feature_toggle", args=[self.club.pk, self.flag.pk])

        self.client.post(url)
        self.assertTrue(self.flag.clubs.filter(pk=self.club.pk).exists())

        self.client.post(url)
        self.assertFalse(self.flag.clubs.filter(pk=self.club.pk).exists())

    def test_toggle_a_switch(self):
        switch = Switch.objects.create(name="maintenance", active=False)

        self.client.post(reverse("controlpanel:switch_toggle", args=[switch.pk]))

        switch.refresh_from_db()
        self.assertTrue(switch.active)

    def test_club_detail_offers_a_toggle_per_feature(self):
        response = self.client.get(reverse("controlpanel:club_detail", args=[self.club.pk]))

        self.assertContains(response, "shop")
        self.assertContains(response, reverse("controlpanel:club_feature_toggle", args=[self.club.pk, self.flag.pk]))

    def test_an_everyone_flag_shows_a_badge_instead_of_a_club_toggle(self):
        # `everyone` overrides club targeting, so offering a per-club toggle would lie.
        self.flag.everyone = True
        self.flag.save()

        response = self.client.get(reverse("controlpanel:club_detail", args=[self.club.pk]))

        self.assertContains(response, "On for all clubs")
        self.assertNotContains(response, reverse("controlpanel:club_feature_toggle", args=[self.club.pk, self.flag.pk]))


class MessageAlertTests(TestCase):
    def alert(self, level, text, extra_tags=None):
        return as_alert(Message(level, text, extra_tags=extra_tags))

    def test_each_level_gets_its_own_icon_title_and_colour(self):
        self.assertEqual(self.alert(messages.SUCCESS, "Saved.")["icon"], "circle-check")
        self.assertEqual(self.alert(messages.WARNING, "Careful.")["css"], "alert-warning")
        self.assertEqual(self.alert(messages.ERROR, "Boom.")["title"], "Something went wrong")
        self.assertEqual(self.alert(messages.INFO, "FYI.")["css"], "alert-info")

    def test_extra_tags_override_the_title(self):
        alert = self.alert(messages.SUCCESS, "Ajax United is live.", extra_tags="Club created")

        self.assertEqual(alert["title"], "Club created")
        self.assertEqual(alert["body"], "Ajax United is live.")
        self.assertEqual(alert["css"], "alert-success")  # a custom title must not change the level

    def test_an_unknown_level_falls_back_to_info(self):
        self.assertEqual(self.alert(999, "Odd.")["css"], "alert-info")


class MessageRenderingTests(ControlPanelTestBase):
    def test_a_message_renders_as_a_soft_alert_with_icon_and_title(self):
        response = self.client.post(reverse("controlpanel:club_archive", args=[self.club.pk]), follow=True)

        self.assertContains(response, "alert alert-soft alert-warning")
        self.assertContains(response, '<div class="font-bold">Careful</div>', html=False)
        self.assertContains(response, "<svg")  # the lucide icon


class FieldRenderingTests(TestCase):
    def field(self, form_field, name="email", errors=False):
        class Form(forms.Form):
            pass

        Form.base_fields[name] = form_field
        form = Form(data={} if errors else None)
        if errors:
            form.full_clean()
        return form[name]

    def test_known_fields_get_an_icon_and_others_do_not(self):
        self.assertEqual(field_icon(self.field(forms.EmailField(), "email")), "mail")
        self.assertEqual(field_icon(self.field(forms.CharField(), "password")), "lock-keyhole")
        self.assertEqual(field_icon(self.field(forms.CharField(), "note")), "")

    def test_the_default_rendering_styles_the_input_itself(self):
        self.assertIn('class="input input-bordered w-full"', str(daisy(self.field(forms.CharField()))))

    def test_an_override_replaces_the_daisy_classes(self):
        # The icon layout puts `input` on the wrapping label, so the input must not
        # carry it too — that would draw a box inside a box.
        rendered = str(daisy(self.field(forms.CharField()), "grow"))

        self.assertIn('class="grow"', rendered)
        self.assertNotIn("input-bordered", rendered)

    def test_an_override_leaves_the_error_state_to_the_wrapper(self):
        rendered = str(daisy(self.field(forms.CharField(required=True), errors=True), "grow"))

        self.assertNotIn("grow-error", rendered)

    def test_the_default_rendering_marks_errors_on_the_input(self):
        rendered = str(daisy(self.field(forms.CharField(required=True), errors=True)))

        self.assertIn("input-error", rendered)


class LoginFormRenderingTests(TestCase):
    def setUp(self):
        self.response = self.client.get(reverse("account_login"))

    def test_the_fields_carry_an_icon_and_no_visible_label(self):
        self.assertContains(self.response, 'class="sr-only">Email</span>')
        self.assertContains(self.response, 'class="sr-only">Password</span>')
        self.assertNotContains(self.response, '<span class="label-text">Email</span>')
        self.assertContains(self.response, 'placeholder="Email address"')

    def test_the_checkbox_keeps_its_visible_label(self):
        self.assertContains(self.response, '<span class="label-text">Remember Me</span>')

    def test_the_password_reset_link_is_spaced_and_addressable(self):
        # The input's aria-describedby points here; without the id it dangles.
        self.assertContains(self.response, 'id="id_password_helptext"')
        self.assertContains(self.response, "mt-3")
        self.assertContains(self.response, "Forgot your password?")


class LoginLayoutTests(TestCase):
    def setUp(self):
        self.response = self.client.get(reverse("account_login"))

    def test_remember_me_is_laid_out_by_the_page_not_the_fields_element(self):
        # It sits on the button row, so the fields element must not also render it.
        self.assertEqual(self.response.content.count(b'name="remember"'), 1)
        self.assertContains(self.response, '<span class="label-text">Remember Me</span>')

    def test_the_passkey_button_sits_beside_sign_in(self):
        self.assertContains(self.response, "btn btn-accent")
        self.assertContains(self.response, "Sign in with a passkey")

    def test_the_passkey_button_has_a_form_to_submit(self):
        # The button posts to the hidden `mfa_login` form via its `form` attribute. That
        # form comes from allauth's extra_body block — without it the button is dead.
        self.assertContains(self.response, 'form="mfa_login"')
        self.assertContains(self.response, 'id="mfa_login"')
        self.assertContains(self.response, "allauth.webauthn.forms.loginForm")


class ExcludedFilterTests(TestCase):
    def field(self, name):
        class Form(forms.Form):
            pass

        Form.base_fields[name] = forms.CharField()
        return Form()[name]

    def test_a_field_is_excluded_by_exact_name(self):
        self.assertTrue(excluded(self.field("remember"), "remember"))

    def test_a_name_that_merely_contains_another_is_not_excluded(self):
        # A substring test would drop "password" when excluding "password2".
        self.assertFalse(excluded(self.field("password"), "password2,remember"))

    def test_nothing_is_excluded_without_a_list(self):
        self.assertFalse(excluded(self.field("remember"), None))


class PlatformAttentionTests(TestCase):
    """The numbers that are supposed to be zero."""

    def setUp(self):
        self.club = Club.objects.create(name="Ajax United")
        self.today = timezone.localdate()

    def season(self, club, start, end):
        return Season.objects.create(club=club, start_date=start, end_date=end)

    def test_a_club_with_no_season_covering_today_is_flagged(self):
        self.assertIn(self.club, clubs_without_a_season())

        self.season(self.club, self.today - datetime.timedelta(days=10), self.today + datetime.timedelta(days=10))

        self.assertNotIn(self.club, clubs_without_a_season())

    def test_a_past_season_does_not_count_as_a_current_one(self):
        self.season(self.club, self.today - datetime.timedelta(days=400), self.today - datetime.timedelta(days=40))

        self.assertIn(self.club, clubs_without_a_season())

    def test_an_archived_club_is_not_chased(self):
        self.club.archive()

        self.assertNotIn(self.club, clubs_without_a_season())
        self.assertNotIn(self.club, dormant_clubs())

    def test_a_club_with_nothing_scheduled_is_dormant(self):
        self.assertIn(self.club, dormant_clubs())

        Event.objects.create(club=self.club, title="Training", start=timezone.now() + datetime.timedelta(days=3))

        self.assertNotIn(self.club, dormant_clubs())

    def test_an_event_beyond_the_horizon_does_not_wake_a_club(self):
        Event.objects.create(club=self.club, title="Far off", start=timezone.now() + datetime.timedelta(days=90))

        self.assertIn(self.club, dormant_clubs())

    def test_a_past_event_does_not_wake_a_club(self):
        Event.objects.create(club=self.club, title="Gone", start=timezone.now() - datetime.timedelta(days=3))

        self.assertIn(self.club, dormant_clubs())


class MfaPendingTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United")

    def test_staff_without_a_second_factor_are_pending(self):
        user = User.objects.create_user(email="staff@example.com", password="pw-secret-123", is_staff=True)

        self.assertIn(user, admins_pending_mfa())

        enrol_mfa(user)

        self.assertNotIn(user, admins_pending_mfa())

    def test_a_club_admin_without_a_second_factor_is_pending(self):
        # They are locked out until they enrol, so this is a support queue.
        user = User.objects.create_user(email="admin@example.com", password="pw-secret-123")
        member = Member.objects.create(first_name="Ada", last_name="Lovelace", user=user)
        ClubRole.objects.create(club=self.club, member=member, role=ClubRole.Roles.ADMIN)

        self.assertIn(user, admins_pending_mfa())

    def test_an_ordinary_member_is_not_chased(self):
        user = User.objects.create_user(email="member@example.com", password="pw-secret-123")
        member = Member.objects.create(first_name="Bob", last_name="Bobson", user=user)
        ClubRole.objects.create(club=self.club, member=member, role=ClubRole.Roles.MEMBER)

        self.assertNotIn(user, admins_pending_mfa())

    def test_each_pending_admin_is_counted_once(self):
        # Two elevated roles in two clubs is still one person to chase.
        other = Club.objects.create(name="Feyenoord")
        user = User.objects.create_user(email="admin@example.com", password="pw-secret-123", is_staff=True)
        member = Member.objects.create(first_name="Ada", last_name="Lovelace", user=user)
        ClubRole.objects.create(club=self.club, member=member, role=ClubRole.Roles.ADMIN)
        ClubRole.objects.create(club=other, member=member, role=ClubRole.Roles.EDITOR)

        self.assertEqual(admins_pending_mfa().count(), 1)


class OnboardingFunnelTests(TestCase):
    def test_the_funnel_narrows_as_clubs_stall(self):
        empty = Club.objects.create(name="Empty FC")  # noqa: F841 — a shell, counted only at step one
        with_member = Club.objects.create(name="Members FC")
        season = Season.objects.create(club=with_member, start_date=timezone.localdate(), end_date=timezone.localdate() + datetime.timedelta(days=30))
        member = Member.objects.create(first_name="Ada", last_name="Lovelace")
        ClubMembership.objects.create(club=with_member, season=season, member=member)

        steps = {step["label"]: step["count"] for step in onboarding_funnel()}

        self.assertEqual(steps["Clubs"], 2)
        self.assertEqual(steps["With members"], 1)
        self.assertEqual(steps["With a team"], 0)
        self.assertEqual(steps["With events"], 0)


class FlagAdoptionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.club = Club.objects.create(name="Ajax United")

    def test_clubs_are_counted_per_flag(self):
        flag = Flag.objects.create(name="shop")
        flag.clubs.add(self.club)

        self.assertEqual(flag_adoption(), [{"name": "shop", "clubs": 1, "everyone": None, "overridden": False}])

    def test_an_everyone_flag_reports_itself_as_overridden(self):
        # `everyone` beats club targeting, so the club count would be a lie.
        Flag.objects.create(name="shop", everyone=True)

        self.assertTrue(flag_adoption()[0]["overridden"])


class PlatformChartTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United")
        self.season = Season.objects.create(club=self.club, start_date=timezone.localdate(), end_date=timezone.localdate() + datetime.timedelta(days=30))
        self.member = Member.objects.create(first_name="Ada", last_name="Lovelace")

    def test_the_series_is_dense(self):
        # Zero-filled: a chart that skips empty months draws a smooth line over a month
        # in which nothing happened.
        series = platform_charts()["signups"]

        self.assertEqual(len(series), 13)
        self.assertTrue(all(point["new"] == 0 and point["returning"] == 0 for point in series))

    def test_signups_land_in_the_month_they_happened(self):
        ClubMembership.objects.create(club=self.club, season=self.season, member=self.member, signed_up_at=timezone.localdate())

        series = platform_charts()["signups"]

        self.assertEqual(series[-1]["new"], 1)

    def test_new_is_per_club_not_per_platform(self):
        # A veteran of one club joining a second is new *there*. Keying "first season" on the
        # member alone would file their very first signup at the new club as a renewal.
        other = Club.objects.create(name="Feyenoord")
        old_season = Season.objects.create(club=other, start_date=timezone.localdate() - datetime.timedelta(days=400), end_date=timezone.localdate() - datetime.timedelta(days=40))
        ClubMembership.objects.create(club=other, season=old_season, member=self.member, signed_up_at=timezone.localdate() - datetime.timedelta(days=300))
        ClubMembership.objects.create(club=self.club, season=self.season, member=self.member, signed_up_at=timezone.localdate())

        self.assertEqual(platform_charts()["signups"][-1]["new"], 1)

    def test_only_paid_orders_count_as_club_revenue(self):
        # `club_revenue` is members paying their clubs. It is NOT platform income, which is
        # why it no longer shares a chart (or a name) with our dues.
        Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("50.00"), status=Order.OrderStatus.PAID)
        Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("30.00"), status=Order.OrderStatus.PENDING)

        self.assertEqual(platform_charts()["club_revenue"][-1]["value"], 50.0)
        self.assertEqual(platform_attention()["outstanding"], Decimal("30.00"))


class DashboardMetricsTests(ControlPanelTestBase):
    def test_the_dashboard_renders_its_metrics_and_charts(self):
        response = self.client.get(reverse("controlpanel:dashboard"))

        self.assertContains(response, "No current season")
        self.assertContains(response, "MFA pending")
        self.assertContains(response, "Payment pending")
        self.assertContains(response, 'id="signups-chart"')
        self.assertContains(response, "js/chart.js")
        self.assertIn("signups", response.context["charts"])


class ClubAttentionTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United")
        self.today = timezone.localdate()
        self.season = Season.objects.create(club=self.club, start_date=self.today - datetime.timedelta(days=30), end_date=self.today + datetime.timedelta(days=300))
        self.member = Member.objects.create(first_name="Ada", last_name="Lovelace")

    def membership(self, member=None, season=None, **kwargs):
        return ClubMembership.objects.create(club=self.club, season=season or self.season, member=member or self.member, **kwargs)

    def test_a_team_with_no_manager_is_flagged(self):
        team = Team.objects.create(club=self.club, name="U15")

        self.assertIn(team, teams_without_a_manager(self.club, self.season))

        coach = Position.objects.create(club=self.club, name="Coach", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=team, member=self.member, season=self.season, position=coach)

        self.assertNotIn(team, teams_without_a_manager(self.club, self.season))

    def test_a_non_management_staffer_does_not_count_as_a_coach(self):
        # Somebody has to be able to pick the squad; a physio cannot.
        team = Team.objects.create(club=self.club, name="U15")
        physio = Position.objects.create(club=self.club, name="Physio", staff_position=True, management_position=False)
        StaffAssignment.objects.create(team=team, member=self.member, season=self.season, position=physio)

        self.assertIn(team, teams_without_a_manager(self.club, self.season))

    def test_a_coach_from_a_previous_season_does_not_count(self):
        old = Season.objects.create(club=self.club, start_date=self.today - datetime.timedelta(days=400), end_date=self.today - datetime.timedelta(days=40))
        team = Team.objects.create(club=self.club, name="U15")
        coach = Position.objects.create(club=self.club, name="Coach", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=team, member=self.member, season=old, position=coach)

        self.assertIn(team, teams_without_a_manager(self.club, self.season))

    def test_an_active_member_on_no_team_is_unrostered(self):
        self.membership(status=ClubMembership.StatusChoices.ACTIVE)

        self.assertIn(self.member, unrostered_members(self.club, self.season))

        team = Team.objects.create(club=self.club, name="U15")
        position = Position.objects.create(club=self.club, name="Forward")
        TeamMembership.objects.create(team=team, member=self.member, season=self.season, position=position, jersey_number=9)

        self.assertNotIn(self.member, unrostered_members(self.club, self.season))

    def test_a_pending_member_is_not_counted_as_unrostered(self):
        # They have not been let in yet, so having no team is expected.
        self.membership(status=ClubMembership.StatusChoices.PENDING)

        self.assertNotIn(self.member, unrostered_members(self.club, self.season))

    def test_renewal_compares_against_the_previous_season(self):
        previous = Season.objects.create(club=self.club, start_date=self.today - datetime.timedelta(days=400), end_date=self.today - datetime.timedelta(days=40))
        stayed = self.member
        left = Member.objects.create(first_name="Bob", last_name="Bobson")
        self.membership(member=stayed, season=previous, status=ClubMembership.StatusChoices.ACTIVE)
        self.membership(member=left, season=previous, status=ClubMembership.StatusChoices.ACTIVE)
        self.membership(member=stayed, season=self.season)

        self.assertEqual(renewal_rate(self.club, self.season), 50)

    def test_a_previous_season_with_nobody_active_yields_no_rate(self):
        # There is a season to compare against but nobody to renew — dividing by that
        # would blow up, and calling it 0% would be a lie.
        previous = Season.objects.create(club=self.club, start_date=self.today - datetime.timedelta(days=400), end_date=self.today - datetime.timedelta(days=40))
        self.membership(season=previous, status=ClubMembership.StatusChoices.LAPSED)

        self.assertIsNone(renewal_rate(self.club, self.season))

    def test_a_first_season_club_has_no_renewal_rate(self):
        # It has not failed to renew anyone; rendering that as 0% would libel it.
        self.membership(status=ClubMembership.StatusChoices.ACTIVE)

        self.assertIsNone(renewal_rate(self.club, self.season))

    def test_unpaid_orders_are_bucketed_by_age(self):
        old = Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("100.00"), status=Order.OrderStatus.PENDING)
        Order.objects.filter(pk=old.pk).update(created=timezone.now() - datetime.timedelta(days=90))
        Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("40.00"), status=Order.OrderStatus.PENDING)
        Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("999.00"), status=Order.OrderStatus.PAID)

        buckets = {bucket["label"]: bucket["total"] for bucket in fee_aging(self.club)}

        self.assertEqual(buckets["0-30 days"], Decimal("40.00"))
        self.assertEqual(buckets["60+ days"], Decimal("100.00"))  # paid orders are not owed

    def test_attendance_is_turnout_of_those_who_answered(self):
        event = Event.objects.create(club=self.club, season=self.season, title="Match", start=timezone.now() - datetime.timedelta(days=1))
        bob = Member.objects.create(first_name="Bob", last_name="Bobson")
        carol = Member.objects.create(first_name="Carol", last_name="Carolson")
        Attendance.objects.create(event=event, member=self.member, status=Attendance.AttendanceStatus.PRESENT)
        Attendance.objects.create(event=event, member=bob, status=Attendance.AttendanceStatus.ABSENT)
        Attendance.objects.create(event=event, member=carol, status=Attendance.AttendanceStatus.NO_RESPONSE)

        rates = attendance_rates(self.club, self.season)

        self.assertEqual(rates["turnout"], 50)  # 1 present of 2 who answered — silence is not an absence
        self.assertEqual(rates["no_response"], 33)  # 1 of 3 never answered

    def test_a_future_event_does_not_drag_turnout_down(self):
        event = Event.objects.create(club=self.club, season=self.season, title="Next week", start=timezone.now() + datetime.timedelta(days=7))
        Attendance.objects.create(event=event, member=self.member, status=Attendance.AttendanceStatus.NO_RESPONSE)

        self.assertIsNone(attendance_rates(self.club, self.season)["turnout"])

    def test_a_club_with_no_season_reports_no_rates(self):
        Season.objects.all().delete()

        attention = club_attention(self.club)

        self.assertTrue(attention["no_season"])
        self.assertEqual(attention["teams_without_manager"], 0)
        self.assertIsNone(attention["renewal_rate"])


class ClubDetailMetricsTests(ControlPanelTestBase):
    def test_the_club_page_renders_its_metrics_and_charts(self):
        response = self.client.get(reverse("controlpanel:club_detail", args=[self.club.pk]))

        self.assertContains(response, "No coach")
        self.assertContains(response, "Unrostered")
        self.assertContains(response, "Unpaid, by age")
        self.assertContains(response, 'id="fees-chart"')
        self.assertIn("fees", response.context["charts"])

    def test_a_club_without_a_season_is_told_it_is_inert(self):
        response = self.client.get(reverse("controlpanel:club_detail", args=[self.club.pk]))

        self.assertContains(response, "cannot take a signup")


class NewMemberTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United")
        self.today = timezone.localdate()
        self.previous = Season.objects.create(club=self.club, start_date=self.today - datetime.timedelta(days=400), end_date=self.today - datetime.timedelta(days=40))
        self.season = Season.objects.create(club=self.club, start_date=self.today - datetime.timedelta(days=30), end_date=self.today + datetime.timedelta(days=300))
        self.veteran = Member.objects.create(first_name="Ada", last_name="Lovelace")
        self.rookie = Member.objects.create(first_name="Bob", last_name="Bobson")

    def membership(self, member, season, signed_up_at=None):
        return ClubMembership.objects.create(club=self.club, season=season, member=member, status=ClubMembership.StatusChoices.ACTIVE, signed_up_at=signed_up_at)

    def test_only_first_timers_count_as_new(self):
        self.membership(self.veteran, self.previous)
        self.membership(self.veteran, self.season)
        self.membership(self.rookie, self.season)

        new = new_members(self.club, self.season)

        self.assertIn(self.rookie, new)
        self.assertNotIn(self.veteran, new)

    def test_a_member_returning_after_a_gap_is_not_new(self):
        # They skipped a season and came back. Counting that as growth would flatter every
        # recovery; they are a renewal.
        self.membership(self.veteran, self.previous)
        gap = Season.objects.create(club=self.club, start_date=self.today - datetime.timedelta(days=39), end_date=self.today - datetime.timedelta(days=31))  # noqa: F841
        self.membership(self.veteran, self.season)

        self.assertNotIn(self.veteran, new_members(self.club, self.season))

    def test_a_member_of_another_club_is_new_here(self):
        # "New" is per club, not per platform.
        other = Club.objects.create(name="Feyenoord")
        other_season = Season.objects.create(club=other, start_date=self.today - datetime.timedelta(days=400), end_date=self.today - datetime.timedelta(days=40))
        ClubMembership.objects.create(club=other, season=other_season, member=self.rookie)
        self.membership(self.rookie, self.season)

        self.assertIn(self.rookie, new_members(self.club, self.season))

    def test_a_club_with_no_season_has_no_new_members(self):
        self.assertEqual(new_members(self.club, None).count(), 0)

    def test_signups_are_split_by_month_into_new_and_returning(self):
        self.membership(self.veteran, self.previous, signed_up_at=self.today - datetime.timedelta(days=200))
        self.membership(self.veteran, self.season, signed_up_at=self.today)
        self.membership(self.rookie, self.season, signed_up_at=self.today)

        this_month = signup_split(self.club)[-1]

        self.assertEqual(this_month["new"], 1)  # the rookie
        self.assertEqual(this_month["returning"], 1)  # the veteran renewing

    def test_a_first_ever_signup_counts_as_new_in_its_own_month(self):
        self.membership(self.veteran, self.previous, signed_up_at=self.today - datetime.timedelta(days=200))

        series = signup_split(self.club)

        self.assertEqual(sum(month["new"] for month in series), 1)
        self.assertEqual(sum(month["returning"] for month in series), 0)


class ClubHealthTableTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.club = Club.objects.create(name="Ajax United")
        self.season = Season.objects.create(club=self.club, start_date=self.today - datetime.timedelta(days=30), end_date=self.today + datetime.timedelta(days=300))
        self.member = Member.objects.create(first_name="Ada", last_name="Lovelace")

    def health(self):
        return clubs_with_health().get(pk=self.club.pk)

    def test_the_whole_table_costs_one_query(self):
        Club.objects.create(name="Feyenoord")

        with self.assertNumQueries(1):
            [(club.active_members, club.outstanding, club.teams_without_coach) for club in clubs_with_health()]

    def test_money_is_not_inflated_by_other_joins(self):
        # The reason each aggregate is its own subquery: a Sum and a Count spanning different
        # joins multiply each other's rows, and the club's debt comes back doubled for every
        # membership it happens to have.
        for name in ("Bob", "Carol", "Dave"):
            member = Member.objects.create(first_name=name, last_name="Bobson")
            ClubMembership.objects.create(club=self.club, season=self.season, member=member, status=ClubMembership.StatusChoices.ACTIVE)
        Order.objects.create(club=self.club, purchaser=self.member, total=Decimal("100.00"), status=Order.OrderStatus.PENDING)

        health = self.health()

        self.assertEqual(health.outstanding, Decimal("100.00"))  # not 300.00
        self.assertEqual(health.active_members, 3)

    def test_a_club_reports_its_missing_coaches(self):
        Team.objects.create(club=self.club, name="U15")
        managed = Team.objects.create(club=self.club, name="U17")
        coach = Position.objects.create(club=self.club, name="Coach", staff_position=True, management_position=True)
        StaffAssignment.objects.create(team=managed, member=self.member, season=self.season, position=coach)

        health = self.health()

        self.assertEqual(health.team_count, 2)
        self.assertEqual(health.teams_without_coach, 1)

    def test_a_club_with_no_season_and_no_events_is_marked(self):
        Season.objects.all().delete()

        health = self.health()

        self.assertFalse(health.has_season)
        self.assertEqual(health.upcoming_events, 0)

    def test_upcoming_events_only_count_the_next_thirty_days(self):
        Event.objects.create(club=self.club, title="Soon", start=timezone.now() + datetime.timedelta(days=3))
        Event.objects.create(club=self.club, title="Far", start=timezone.now() + datetime.timedelta(days=90))

        self.assertEqual(self.health().upcoming_events, 1)

    def test_the_dashboard_table_shows_health_not_vanity(self):
        user = User.objects.create_user(email="root@example.com", password="pw-secret-123", is_staff=True)
        enrol_mfa(user)
        self.client.force_login(user)

        response = self.client.get(reverse("controlpanel:dashboard"))

        # Health, not vanity: Plan and Dues each name something to act on, next to the counts.
        for column in ("Members", "Admins", "Teams", "Events", "Plan", "Dues"):
            self.assertContains(response, f">{column}</th>")


class ClubListHealthTests(ControlPanelTestBase):
    def test_the_list_shows_the_same_health_columns_as_the_dashboard(self):
        response = self.client.get(reverse("controlpanel:club_list"))

        for column in ("Members", "Admins", "Teams", "Events", "Plan", "Dues"):
            self.assertContains(response, f">{column}</th>")
        self.assertTemplateUsed(response, "controlpanel/_club_health_table.html")

    def test_an_archived_club_is_badged_archived_rather_than_dormant(self):
        # Its subdomain does not resolve, so "nothing scheduled" is not news.
        self.club.archive()

        response = self.client.get(reverse("controlpanel:club_list"), {"archived": "1"})

        self.assertContains(response, "Archived")
        self.assertNotContains(response, "Dormant")

    def test_searching_keeps_the_health_annotations(self):
        response = self.client.get(reverse("controlpanel:club_list"), {"q": "Ajax"})

        club = response.context["clubs"][0]

        self.assertEqual(club.active_members, 0)
        self.assertEqual(club.teams_without_coach, 0)

    def test_the_list_does_not_fan_out_per_club(self):
        for name in ("Feyenoord", "PSV", "Twente"):
            Club.objects.create(name=name)

        with self.assertNumQueries(1):
            [(club.outstanding, club.upcoming_events) for club in clubs_with_health()]


class TemplateCommentTests(TestCase):
    def test_no_template_uses_a_multiline_hash_comment(self):
        """Django's {# #} is single-line only — its lexer regex is not DOTALL, so a
        multi-line one is not a comment at all: it renders to the page as text."""
        templates = [path for path in pathlib.Path(settings.BASE_DIR).glob("**/templates/**/*.html") if ".venv" not in path.parts and "node_modules" not in path.parts]
        offenders = [f"{path.relative_to(settings.BASE_DIR)}:{number}" for path in templates for number, line in enumerate(path.read_text().splitlines(), start=1) if "{#" in line and "#}" not in line]

        self.assertTrue(templates)  # the glob must actually be finding our templates
        self.assertEqual(offenders, [], "use {% comment %} for multi-line comments")


class PlatformDuesMetricTests(TestCase):
    """What the clubs owe US — kept strictly apart from what members owe their clubs."""

    def setUp(self):
        self.today = timezone.localdate()
        self.club = Club.objects.create(name="Ajax United")
        self.tier = Tier.objects.create(name="Standard")
        TierPrice.objects.create(tier=self.tier, active_from=self.today - datetime.timedelta(days=1200), amount=Decimal("500.00"))

    def test_dues_owed_is_the_unpaid_balance_across_every_club(self):
        subscribe(self.club, self.tier)
        record_payment(self.club.dues.first(), Decimal("200.00"))

        self.assertEqual(platform_attention()["dues_owed"], Decimal("300.00"))

    def test_grace_and_overdue_are_counted_separately(self):
        in_grace = Club.objects.create(name="Grace FC")
        overdue = Club.objects.create(name="Overdue FC")
        subscribe(in_grace, self.tier, start=self.today - datetime.timedelta(days=370))
        subscribe(overdue, self.tier, start=self.today - datetime.timedelta(days=365 + GRACE_DAYS + 10))

        attention = platform_attention()

        self.assertEqual(attention["dues_in_grace"], 1)
        self.assertEqual(attention["dues_overdue"], 1)

    def test_clubs_on_no_tier_are_flagged(self):
        self.assertEqual(platform_attention()["clubs_unbilled"], 1)

        subscribe(self.club, self.tier)

        self.assertEqual(platform_attention()["clubs_unbilled"], 0)

    def test_renewals_pending_counts_clubs_about_to_lapse(self):
        # ~0 in normal running; a number here means the renewal cron has stopped.
        self.assertEqual(platform_attention()["renewals_pending"], 0)

        subscribe(self.club, self.tier, start=self.today - datetime.timedelta(days=350))  # ends in 15 days

        self.assertEqual(platform_attention()["renewals_pending"], 1)

    def test_the_dashboard_surfaces_pending_renewals(self):
        # The whole point of the KPI: a club about to go free is visible, though nothing is
        # owed yet, so no other figure on the page would show it.
        subscribe(self.club, self.tier, start=self.today - datetime.timedelta(days=350))
        staff = User.objects.create_user(email="staff@example.com", password="pw-secret-123", is_staff=True)
        enrol_mfa(staff)
        self.client.force_login(staff)

        self.assertContains(self.client.get(reverse("controlpanel:dashboard")), "awaiting renewal")

    def test_platform_dues_and_club_shop_money_are_different_charts(self):
        subscribe(self.club, self.tier)
        record_payment(self.club.dues.first(), Decimal("500.00"))

        charts = platform_charts()

        self.assertEqual(charts["dues"][-1]["value"], 500.0)
        self.assertEqual(charts["club_revenue"][-1]["value"], 0.0)  # never ours

    def test_the_health_table_carries_the_plan_and_what_is_owed(self):
        subscribe(self.club, self.tier)

        club = clubs_with_health().get(pk=self.club.pk)

        self.assertEqual(club.tier_name, "Standard")
        self.assertEqual(club.dues_owed, Decimal("500.00"))

    def test_a_fully_paid_club_shows_when_its_cover_ends(self):
        # The end of the current paid period is the day grace would start if nothing renews.
        subscribe(self.club, self.tier)
        due = self.club.dues.first()
        record_payment(due, due.amount)

        club = clubs_with_health().get(pk=self.club.pk)

        self.assertEqual(club.covered_until, due.period_end)
        self.assertEqual(club.covered_status, Due.Status.PAID)

    def test_a_waived_period_also_shows_its_cover_end(self):
        # Waived is settled too — the club is covered for that time, so its end date shows,
        # badged "waived" rather than "paid".
        subscribe(self.club, self.tier)
        due = self.club.dues.first()
        waive(due)

        club = clubs_with_health().get(pk=self.club.pk)

        self.assertEqual(club.covered_until, due.period_end)
        self.assertEqual(club.covered_status, Due.Status.WAIVED)

    def test_a_club_that_owes_has_no_cover(self):
        subscribe(self.club, self.tier)  # unpaid

        club = clubs_with_health().get(pk=self.club.pk)
        self.assertIsNone(club.covered_until)
        self.assertIsNone(club.covered_status)

    def test_the_health_table_still_costs_one_query_with_billing_on_it(self):
        subscribe(self.club, self.tier)
        subscribe(Club.objects.create(name="Feyenoord"), self.tier)

        with self.assertNumQueries(1):
            [(club.tier_name, club.dues_owed, club.outstanding) for club in clubs_with_health()]


class BillingPanelTests(ControlPanelTestBase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.tier = Tier.objects.create(name="Standard")
        TierPrice.objects.create(tier=self.tier, active_from=self.today - datetime.timedelta(days=1200), amount=Decimal("500.00"))

    def test_the_billing_page_lists_tiers_and_what_is_owed(self):
        subscribe(self.club, self.tier)

        response = self.client.get(reverse("controlpanel:billing"))

        self.assertContains(response, "Standard")
        self.assertContains(response, "500.00")

    def test_a_tier_can_be_created_and_priced(self):
        self.client.post(reverse("controlpanel:tier_create"), {"name": "Large", "description": "", "is_active": "on"})
        tier = Tier.objects.get(name="Large")

        self.client.post(reverse("controlpanel:tier_price_create", args=[tier.pk]), {"active_from": self.today.isoformat(), "amount": "900.00"})

        self.assertEqual(tier.price_on(self.today), Decimal("900.00"))

    def test_a_rate_change_does_not_rewrite_an_open_period(self):
        subscribe(self.club, self.tier)

        self.client.post(reverse("controlpanel:tier_price_create", args=[self.tier.pk]), {"active_from": self.today.isoformat(), "amount": "900.00"})

        self.assertEqual(self.club.dues.first().amount, Decimal("500.00"))

    def test_subscribing_a_club_opens_its_first_period(self):
        self.client.post(reverse("controlpanel:club_subscribe", args=[self.club.pk]), {"tier": self.tier.pk, "auto_archive": "on", "notes": ""})

        self.assertEqual(self.club.dues.count(), 1)
        self.assertEqual(self.club.subscription.tier, self.tier)

    def test_a_payment_can_be_recorded_and_settles_the_due(self):
        subscribe(self.club, self.tier)
        due = self.club.dues.first()

        self.client.post(reverse("controlpanel:due_pay", args=[due.pk]), {"amount": "500.00", "method": "bank_transfer", "reference": "TRX-1", "paid_at": "", "note": ""})

        due.refresh_from_db()
        self.assertEqual(due.status, Due.Status.PAID)
        self.assertEqual(due.payments.first().recorded_by, self.staff)

    def test_a_part_payment_leaves_a_balance(self):
        subscribe(self.club, self.tier)
        due = self.club.dues.first()

        self.client.post(reverse("controlpanel:due_pay", args=[due.pk]), {"amount": "200.00", "method": "bank_transfer", "reference": "", "paid_at": "", "note": ""})

        due.refresh_from_db()
        self.assertEqual(due.balance, Decimal("300.00"))

    def test_a_billing_error_is_shown_rather_than_raised(self):
        # A waived period cannot take a payment; the panel must say so, not 500.
        subscribe(self.club, self.tier)
        due = self.club.dues.first()
        self.client.post(reverse("controlpanel:due_waive", args=[due.pk]))

        response = self.client.post(reverse("controlpanel:due_pay", args=[due.pk]), {"amount": "50.00", "method": "cash", "reference": "", "paid_at": "", "note": ""}, follow=True)

        self.assertContains(response, "cannot take a payment")

    def test_a_period_can_be_waived(self):
        subscribe(self.club, self.tier)
        due = self.club.dues.first()

        self.client.post(reverse("controlpanel:due_waive", args=[due.pk]))

        due.refresh_from_db()
        self.assertEqual(due.status, Due.Status.WAIVED)

    def test_opening_a_period_continues_from_the_last_one(self):
        subscribe(self.club, self.tier, start=self.today - datetime.timedelta(days=400))
        first = self.club.dues.first()

        self.client.post(reverse("controlpanel:club_open_period", args=[self.club.pk]), {"start": ""})

        latest = self.club.dues.order_by("-period_start").first()
        self.assertEqual(latest.period_start, first.period_end + datetime.timedelta(days=1))

    def test_reactivating_an_archived_club_restores_it(self):
        subscribe(self.club, self.tier, start=self.today - datetime.timedelta(days=400))
        self.club.archive()

        self.client.post(reverse("controlpanel:club_open_period", args=[self.club.pk]), {"start": self.today.isoformat()})

        self.club.refresh_from_db()
        self.assertFalse(self.club.is_archived)

    def test_the_club_page_shows_the_plan_and_its_periods(self):
        subscribe(self.club, self.tier)

        response = self.client.get(reverse("controlpanel:club_detail", args=[self.club.pk]))

        self.assertContains(response, "Standard")
        self.assertContains(response, "INV-")

    def test_an_invoice_downloads_as_a_pdf(self):
        subscribe(self.club, self.tier)
        due = self.club.dues.first()

        with mock.patch("controlpanel.views.invoice_pdf", return_value=b"%PDF-1.7 fake"):
            response = self.client.get(reverse("controlpanel:due_invoice", args=[due.pk]))

        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(due.invoice.number, response["Content-Disposition"])

    def test_a_missing_pdf_library_is_reported_rather_than_a_500(self):
        # WeasyPrint needs native libs. Without them the button must explain itself.
        subscribe(self.club, self.tier)
        due = self.club.dues.first()

        with mock.patch("controlpanel.views.invoice_pdf", side_effect=BillingError("PDF rendering needs the native pango/cairo libraries.")):
            response = self.client.get(reverse("controlpanel:due_invoice", args=[due.pk]), follow=True)

        self.assertContains(response, "pango")


class BillingFormRenderTests(ControlPanelTestBase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.tier = Tier.objects.create(name="Standard")
        TierPrice.objects.create(tier=self.tier, active_from=self.today - datetime.timedelta(days=1200), amount=Decimal("500.00"))

    def test_the_billing_forms_render(self):
        subscribe(self.club, self.tier)
        due = self.club.dues.first()

        for url in (
            reverse("controlpanel:tier_create"),
            reverse("controlpanel:tier_update", args=[self.tier.pk]),
            reverse("controlpanel:tier_price_create", args=[self.tier.pk]),
            reverse("controlpanel:club_subscribe", args=[self.club.pk]),
            reverse("controlpanel:club_open_period", args=[self.club.pk]),
            reverse("controlpanel:due_pay", args=[due.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_the_payment_form_defaults_to_the_outstanding_balance(self):
        subscribe(self.club, self.tier)
        due = self.club.dues.first()
        record_payment(due, Decimal("200.00"))
        due.refresh_from_db()

        response = self.client.get(reverse("controlpanel:due_pay", args=[due.pk]))

        self.assertEqual(response.context["form"].initial["amount"], Decimal("300.00"))

    def test_a_tier_can_be_renamed(self):
        self.client.post(reverse("controlpanel:tier_update", args=[self.tier.pk]), {"name": "Standard plus", "description": "", "is_active": "on"})

        self.tier.refresh_from_db()
        self.assertEqual(self.tier.name, "Standard plus")

    def test_changing_tier_leaves_the_open_period_alone(self):
        # The current period keeps the amount it was issued at; the new rate bites next time.
        subscribe(self.club, self.tier)
        premium = Tier.objects.create(name="Premium")
        TierPrice.objects.create(tier=premium, active_from=self.today, amount=Decimal("900.00"))

        response = self.client.post(reverse("controlpanel:club_subscribe", args=[self.club.pk]), {"tier": premium.pk, "auto_archive": "on", "notes": ""}, follow=True)

        self.club.refresh_from_db()
        self.assertEqual(self.club.subscription.tier, premium)
        self.assertEqual(self.club.dues.first().amount, Decimal("500.00"))
        self.assertContains(response, "keeps the amount it was billed at")

    def test_subscribing_to_an_unpriced_tier_reports_itself(self):
        unpriced = Tier.objects.create(name="Enterprise")

        response = self.client.post(reverse("controlpanel:club_subscribe", args=[self.club.pk]), {"tier": unpriced.pk, "auto_archive": "on", "notes": ""}, follow=True)

        self.assertContains(response, "no price in force")

    def test_billing_a_period_twice_reports_itself(self):
        subscribe(self.club, self.tier)
        start = self.club.dues.first().period_start

        response = self.client.post(reverse("controlpanel:club_open_period", args=[self.club.pk]), {"start": start.isoformat()}, follow=True)

        self.assertContains(response, "already billed")

    def test_waiving_a_paid_period_reports_itself(self):
        subscribe(self.club, self.tier)
        due = self.club.dues.first()
        record_payment(due, Decimal("500.00"))

        response = self.client.post(reverse("controlpanel:due_waive", args=[due.pk]), follow=True)

        self.assertContains(response, "remove them before waiving")


class MaintenancePanelTests(ControlPanelTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_closing_the_platform_records_who_did_it(self):
        self.client.post(reverse("controlpanel:maintenance"), {"message": "Database upgrade."})

        maintenance = Maintenance.current()
        self.assertTrue(maintenance.is_active)
        self.assertEqual(maintenance.message, "Database upgrade.")
        self.assertEqual(maintenance.started_by, self.staff)

    def test_posting_again_reopens_the_platform(self):
        Maintenance.start(message="x", user=self.staff)

        self.client.post(reverse("controlpanel:maintenance"), {})

        self.assertFalse(Maintenance.is_on())

    def test_every_panel_page_warns_while_the_platform_is_closed(self):
        # Not a state to leave on by accident.
        Maintenance.start(user=self.staff)

        for url in (reverse("controlpanel:dashboard"), reverse("controlpanel:club_list"), reverse("controlpanel:features")):
            self.assertContains(self.client.get(url), "closed for maintenance", msg_prefix=url)

    def test_the_features_page_offers_the_switch(self):
        response = self.client.get(reverse("controlpanel:features"))

        self.assertContains(response, "Maintenance mode")
        self.assertContains(response, "Close the platform")
