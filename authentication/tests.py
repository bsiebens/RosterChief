import importlib
import re
import uuid
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

from allauth.account.models import EmailAddress
from allauth.core import context
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from club.models import Club, ClubRole
from controlpanel.services.admins import grant_club_admin
from features.models import EmailSuppression
from members.models import Member
from rosterchief.test_support import enrol_mfa

from .adapters import RosterChiefAccountAdapter, RosterChiefMFAAdapter, webauthn_rp_id
from .middleware import RequireMFAMiddleware, mfa_required_for

User = get_user_model()


def verified_user(email, password, **extra):
    """An account whose login address is already confirmed -- what signing in
    requires under ACCOUNT_EMAIL_VERIFICATION = "mandatory"."""
    user = User.objects.create_user(email=email, password=password, **extra)
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    return user


class UserManagerTests(TestCase):
    def test_create_user_defaults(self):
        user = User.objects.create_user(email="alice@example.com", password="secret123")

        self.assertEqual(user.email, "alice@example.com")
        self.assertTrue(user.check_password("secret123"))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.is_active)

    def test_create_user_requires_email(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password="secret123")

    def test_create_user_normalizes_email_domain(self):
        # BaseUserManager lowercases the domain part of the address.
        user = User.objects.create_user(email="Bob@Example.COM", password="secret123")

        self.assertEqual(user.email, "Bob@example.com")

    def test_create_user_password_is_hashed(self):
        user = User.objects.create_user(email="carol@example.com", password="secret123")

        self.assertNotEqual(user.password, "secret123")

    def test_create_user_without_password_is_unusable(self):
        user = User.objects.create_user(email="dave@example.com")

        self.assertFalse(user.has_usable_password())

    def test_create_superuser_defaults(self):
        admin = User.objects.create_superuser(email="admin@example.com", password="secret123")

        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_active)

    def test_create_superuser_rejects_non_staff(self):
        with self.assertRaises(ValueError):
            User.objects.create_superuser(email="admin@example.com", password="x", is_staff=False)

    def test_create_superuser_rejects_non_superuser(self):
        with self.assertRaises(ValueError):
            User.objects.create_superuser(email="admin@example.com", password="x", is_superuser=False)


class UserModelTests(TestCase):
    def test_email_is_username_field(self):
        self.assertEqual(User.USERNAME_FIELD, "email")
        self.assertEqual(User.REQUIRED_FIELDS, [])

    def test_email_is_unique(self):
        User.objects.create_user(email="dup@example.com", password="x")
        with self.assertRaises(IntegrityError):
            User.objects.create_user(email="dup@example.com", password="y")

    def test_pk_is_uuid(self):
        user = User.objects.create_user(email="uuid@example.com", password="x")
        self.assertIsInstance(user.pk, uuid.UUID)

    def test_str_and_names_fall_back_to_email_without_member(self):
        user = User.objects.create_user(email="lonely@example.com", password="x")

        self.assertEqual(str(user), "lonely@example.com")
        self.assertEqual(user.get_full_name(), "lonely@example.com")
        self.assertEqual(user.get_short_name(), "lonely@example.com")

    def test_str_and_names_use_linked_member(self):
        user = User.objects.create_user(email="linked@example.com", password="x")
        Member.objects.create(user=user, first_name="Jane", last_name="Doe")

        # Re-fetch so the reverse OneToOne relation is resolved from the DB.
        user = User.objects.get(pk=user.pk)

        self.assertEqual(str(user), "Jane Doe")
        self.assertEqual(user.get_full_name(), "Jane Doe")
        self.assertEqual(user.get_short_name(), "Jane")


@override_settings(
    ROSTERCHIEF_BASE_DOMAIN="rosterchief.app",
    MFA_WEBAUTHN_RP_NAME="RosterChief",
    ALLOWED_HOSTS=[".rosterchief.app", "example.test"],
)
class WebAuthnRelyingPartyTests(TestCase):
    """A passkey is bound to a Relying Party ID (a domain).

    allauth's default RP ID is the request host, which under our subdomain
    tenancy would bind a passkey to a single club. We pin it to the registrable
    parent domain so ONE passkey works across every club.
    """

    def rp_entity(self, host):
        request = RequestFactory().get("/", HTTP_HOST=host)
        with context.request_context(request):
            return RosterChiefMFAAdapter().get_public_key_credential_rp_entity()

    def test_rp_id_is_the_parent_domain_not_the_club_subdomain(self):
        self.assertEqual(self.rp_entity("ajax-united.rosterchief.app")["id"], "rosterchief.app")

    def test_rp_id_is_identical_across_clubs(self):
        # The whole point: a passkey registered at one club works at the others.
        here = self.rp_entity("ajax-united.rosterchief.app")
        there = self.rp_entity("rival-fc.rosterchief.app")

        self.assertEqual(here["id"], there["id"])

    def test_rp_name_comes_from_settings(self):
        self.assertEqual(self.rp_entity("ajax-united.rosterchief.app")["name"], "RosterChief")

    @override_settings(ROSTERCHIEF_BASE_DOMAIN="")
    def test_falls_back_to_the_request_host_without_a_base_domain(self):
        request = RequestFactory().get("/", HTTP_HOST="example.test:8000")

        with context.request_context(request):
            self.assertEqual(webauthn_rp_id(), "example.test")


class MFARequirementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Read-only for every test here: each one brings its own user and role.
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def make_user(self, email, **kwargs):
        return User.objects.create_user(email=email, password="pw-secret-123", **kwargs)

    def with_role(self, user, role):
        member = Member.objects.create(user=user, first_name="Ada", last_name="Min")
        ClubRole.objects.create(club=self.club, member=member, role=role)
        return user

    def test_staff_must_have_mfa(self):
        self.assertTrue(mfa_required_for(self.make_user("staff@example.com", is_staff=True)))

    def test_superuser_must_have_mfa(self):
        self.assertTrue(mfa_required_for(User.objects.create_superuser(email="root@example.com", password="pw-secret-123")))

    def test_club_admin_must_have_mfa(self):
        user = self.with_role(self.make_user("admin@example.com"), ClubRole.Roles.ADMIN)

        self.assertTrue(mfa_required_for(user))

    def test_editor_must_have_mfa(self):
        user = self.with_role(self.make_user("editor@example.com"), ClubRole.Roles.EDITOR)

        self.assertTrue(mfa_required_for(user))

    def test_plain_member_does_not_need_mfa(self):
        user = self.with_role(self.make_user("member@example.com"), ClubRole.Roles.MEMBER)

        self.assertFalse(mfa_required_for(user))

    def test_user_without_any_role_does_not_need_mfa(self):
        self.assertFalse(mfa_required_for(self.make_user("nobody@example.com")))


class RequireMFAMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = RequireMFAMiddleware(lambda request: HttpResponse("ok"))

    def dispatch(self, user, path="/"):
        request = self.factory.get(path)
        request.user = user
        return self.middleware(request)

    def make_staff(self):
        return User.objects.create_user(email="staff@example.com", password="pw-secret-123", is_staff=True)

    def test_anonymous_passes_through(self):
        self.assertEqual(self.dispatch(AnonymousUser()).content, b"ok")

    def test_unprivileged_user_passes_through(self):
        user = User.objects.create_user(email="plain@example.com", password="pw-secret-123")

        self.assertEqual(self.dispatch(user).content, b"ok")

    def test_privileged_user_without_mfa_is_sent_to_enrolment(self):
        response = self.dispatch(self.make_staff())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("mfa_index"))

    def test_privileged_user_can_still_reach_the_enrolment_pages(self):
        # Otherwise they'd be redirected in a loop and could never enrol.
        response = self.dispatch(self.make_staff(), path="/accounts/2fa/totp/activate/")

        self.assertEqual(response.content, b"ok")

    def test_enrolled_privileged_user_passes_through(self):
        staff = self.make_staff()
        enrol_mfa(staff)

        self.assertEqual(self.dispatch(staff).content, b"ok")


class AdminLoginRoutingTests(TestCase):
    def test_admin_login_is_routed_through_allauth(self):
        # Django's own admin login knows nothing about second factors.
        response = self.client.get("/admin/login/", {"next": "/admin/"})

        self.assertEqual(response.status_code, 302)
        redirect = urlparse(response.url)
        self.assertEqual(redirect.path, reverse("account_login"))
        # The original destination survives the hop (percent-encoded).
        self.assertEqual(parse_qs(redirect.query)["next"], ["/admin/"])


class AuthFormRenderingTests(TestCase):
    """Every allauth form must actually render its fields.

    Regression: the `fields` element passed `attrs.exclude` straight into a filter.
    On a page that never sets it, resolving a filter *argument* raises
    VariableDoesNotExist — which Django swallows inside {% if %} and reads as false —
    so every field was silently dropped from every form except the login page (the one
    page that does pass `exclude`).
    """

    def test_the_login_form_renders_its_fields(self):
        self.assertContains(self.client.get(reverse("account_login")), 'name="login"')

    def test_the_password_reset_form_renders_its_fields(self):
        self.assertContains(self.client.get(reverse("account_reset_password")), 'name="email"')

    def test_self_registration_is_closed(self):
        # A club has no reason to let a stranger create an account: they're made by
        # an admin or by the family-registration form. The route is shadowed rather
        # than removed so that the `account_signup` name allauth's own templates
        # reverse still resolves.
        response = self.client.get(reverse("account_signup"))

        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, 'name="password1"', status_code=403)


@override_settings(
    ROSTERCHIEF_BASE_DOMAIN="rosterchief.app",
    ALLOWED_HOSTS=["rosterchief.app", "ajax-united.rosterchief.app", "testserver"],
)
class PasswordResetEmailTests(TestCase):
    """allauth auto-attaches templates/account/email/password_reset_key_message.html
    as an HTML alternative next to its own .txt body -- see
    allauth.account.adapter.DefaultAccountAdapter.render_mail, which looks for
    "<prefix>_message.<ext>" for ext in [TEMPLATE_EXTENSION ("html", unset here), "txt"].
    No Python override needed; this only exercises the template."""

    def test_the_html_email_carries_the_clubs_branding_on_a_club_subdomain(self):
        Club.objects.create(name="Ajax United", slug="ajax-united")
        User.objects.create_user(email="parent@example.com", password="pw-secret-123")

        response = self.client.post(reverse("account_reset_password"), {"email": "parent@example.com"}, HTTP_HOST="ajax-united.rosterchief.app")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        [(html_body, mimetype)] = mail.outbox[0].alternatives
        self.assertEqual(mimetype, "text/html")
        self.assertIn("Ajax United", html_body)
        self.assertIn("/accounts/password/reset/key/", html_body)

    def test_the_html_email_falls_back_to_rosterchief_branding_off_a_club_subdomain(self):
        # The base domain has no tenant, e.g. a platform control-panel user
        # resetting their own password -- club.context_processors.branding
        # leaves `club` unset there, so the template must not assume one.
        User.objects.create_user(email="admin@example.com", password="pw-secret-123")

        self.client.post(reverse("account_reset_password"), {"email": "admin@example.com"})

        self.assertEqual(len(mail.outbox), 1)
        [(html_body, mimetype)] = mail.outbox[0].alternatives
        self.assertEqual(mimetype, "text/html")
        # The wordmark splits "Chief" into its own <span> for the sky-blue accent
        # (matching templates/_platform_base.html), so the two halves aren't
        # contiguous text in the raw HTML -- check for both rather than the
        # combined word.
        self.assertIn("Roster", html_body)
        self.assertIn("Chief", html_body)

    def test_the_password_reset_key_html_template_renders_directly(self):
        # Mirrors authentication.tests.AuthFormRenderingTests' direct-render
        # style: exercises the template's own branching (club vs. none, logo
        # vs. initials) without going through the full request/email pipeline.
        club = Club.objects.create(name="Ajax United", slug="ajax-united", primary_color="#1e40af")

        with_club = render_to_string("account/email/password_reset_key_message.html", {"club": club, "password_reset_url": "https://ajax-united.rosterchief.app/accounts/password/reset/key/abc-def/"})
        self.assertIn("Ajax United", with_club)
        self.assertIn("AU", with_club)  # initials fallback: no logo set
        self.assertIn("https://ajax-united.rosterchief.app/accounts/password/reset/key/abc-def/", with_club)

    def test_the_initials_badge_text_contrasts_against_the_fallback_colour(self):
        # Club has no secondary_color, so the badge falls back to #ec4899 --
        # black text (contrast_color("#ec4899")) reads far better on it than
        # the white the template used to hardcode. See
        # club/templatetags/club_email.py::contrast_color.
        club = Club.objects.create(name="Ajax United", slug="ajax-united")

        rendered = render_to_string("account/email/password_reset_key_message.html", {"club": club, "password_reset_url": "https://ajax-united.rosterchief.app/accounts/password/reset/key/abc-def/"})

        self.assertIn("background-color:#ec4899", rendered)
        self.assertIn("color:#000000", rendered)

    def test_the_reset_button_text_contrasts_against_the_no_club_fallback_colour(self):
        # No club at all (the base-domain flow) -- the button falls back to
        # #0ea5e9, RosterChief's own sky blue, which also needs black text
        # for a passing contrast ratio, not the white previously hardcoded.
        rendered = render_to_string("account/email/password_reset_key_message.html", {"club": None, "password_reset_url": "https://rosterchief.app/accounts/password/reset/key/abc-def/", "current_site": None})

        self.assertIn("background-color:#0ea5e9", rendered)
        self.assertIn("color:#000000", rendered)

        without_club = render_to_string("account/email/password_reset_key_message.html", {"club": None, "password_reset_url": "https://rosterchief.app/accounts/password/reset/key/abc-def/", "current_site": SimpleNamespace(name="rosterchief.app")})
        self.assertIn("Roster", without_club)
        self.assertIn("Chief", without_club)
        self.assertIn("https://rosterchief.app/accounts/password/reset/key/abc-def/", without_club)


class RosterChiefAccountAdapterTests(TestCase):
    """RosterChiefAccountAdapter.send_mail -- routes allauth's own mail through
    rosterchief.mail.send_message, exempting only password reset. See
    features.models.EmailSuppression's own docstring for the full "pause
    automated email" picture, and PasswordResetEmailTests above for the same
    exemption exercised through the real view/email pipeline instead of a
    mocked render_mail."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def send(self, template_prefix):
        adapter = RosterChiefAccountAdapter()
        request = RequestFactory().get("/")
        message = mock.Mock()
        with context.request_context(request), mock.patch.object(adapter, "render_mail", return_value=message):
            adapter.send_mail(template_prefix, "someone@example.com", {})
        return message

    def test_password_reset_sends_normally_when_not_suppressed(self):
        message = self.send(RosterChiefAccountAdapter.PASSWORD_RESET_TEMPLATE_PREFIX)

        message.send.assert_called_once()

    def test_password_reset_still_sends_while_suppressed(self):
        EmailSuppression.start()

        message = self.send(RosterChiefAccountAdapter.PASSWORD_RESET_TEMPLATE_PREFIX)

        message.send.assert_called_once()

    def test_email_confirmation_still_sends_while_suppressed(self):
        # Signing in waits on a confirmed address (ACCOUNT_EMAIL_VERIFICATION =
        # "mandatory"), so the confirmation link is as essential as a reset link.
        EmailSuppression.start()

        for prefix in ("account/email/email_confirmation", "account/email/email_confirmation_signup"):
            with self.subTest(prefix=prefix):
                self.send(prefix).send.assert_called_once()

    def test_other_allauth_mail_sends_normally_when_not_suppressed(self):
        message = self.send("account/email/password_changed")

        message.send.assert_called_once()

    def test_other_allauth_mail_is_silenced_while_suppressed(self):
        EmailSuppression.start()

        message = self.send("account/email/password_changed")

        message.send.assert_not_called()


class TwoFactorPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        enrol_mfa(verified_user(email="mfa@example.com", password="pw-secret-123"))

    def setUp(self):
        # The test client is per-test, so the sign-in itself cannot be hoisted.
        # Password accepted, second factor still owed: this is the 2FA challenge page.
        self.response = self.client.post(reverse("account_login"), {"login": "mfa@example.com", "password": "pw-secret-123"}, follow=True)

    def test_the_code_field_renders_as_an_otp_input(self):
        self.assertContains(self.response, 'name="code"')
        self.assertContains(self.response, "otp otp-lg")

    def test_the_input_comes_after_the_boxes(self):
        # daisyUI places each box with nth-child, which counts every child. With the input
        # first, all six boxes shift a stride right, the container grows to seven strides
        # and the ::after focus marker appears as a phantom seventh box.
        html = self.response.content.decode()
        otp = html[html.index('class="otp otp-lg"') : html.index('name="code"')]

        self.assertEqual(otp.count("<span></span>"), 6)

    def test_the_boxes_are_wrapped_in_a_label_so_tapping_focuses_the_input(self):
        # daisyUI's overlaid otp input carries `pointer-events: none` (so clicks land on the
        # boxes, not a naked input) — which also means a tap on the boxes never reaches the
        # input directly. A <label for> is what closes that gap: browsers focus a labelled
        # control on click regardless of the control's own pointer-events. Without this
        # wrapper the field cannot be entered on a touchscreen, which has no Tab key to fall
        # back on.
        html = self.response.content.decode()
        label_start = html.index('<label class="contents"')
        otp_start = html.index('class="otp otp-lg"')

        self.assertLess(label_start, otp_start)
        self.assertIn('for="id_code"', html[label_start : label_start + 60])

    def test_the_otp_field_has_no_placeholder(self):
        # allauth sets placeholder="Code"; inside the boxes it reads as a typed-in code.
        self.assertNotContains(self.response, 'placeholder="Code"')

    def test_cancel_sits_beside_sign_in_and_is_not_primary(self):
        self.assertContains(self.response, '<button class="btn btn-outline btn-neutral gap-2" type="submit" form="logout-from-stage">')
        self.assertContains(self.response, '<button class="btn btn-primary gap-2" type="submit">')

    def test_cancel_has_a_form_to_submit(self):
        self.assertContains(self.response, 'id="logout-from-stage"')

    def test_the_security_key_button_is_an_accent_button_with_a_working_form(self):
        self.assertContains(self.response, "btn btn-accent")
        self.assertContains(self.response, 'form="webauthn_form"')
        # The id lives on the form element — without it the button submits nothing.
        self.assertContains(self.response, 'id="webauthn_form"')
        self.assertContains(self.response, "allauth.webauthn.forms.authenticateForm")


class MfaPageTests(TestCase):
    """Every MFA screen must render. They are built from allauth's `element` primitives,
    so styling lives in the element overrides rather than in eight page templates."""

    @classmethod
    def setUpTestData(cls):
        cls.user = verified_user(email="mfa@example.com", password="pw-secret-123")

    def setUp(self):
        # A real password login (not force_login) so allauth counts it as a recent
        # authentication and doesn't bounce the sensitive pages to reauthenticate.
        self.client.post(reverse("account_login"), {"login": "mfa@example.com", "password": "pw-secret-123"}, follow=True)

    def test_the_manage_page_renders_a_panel_per_authenticator(self):
        response = self.client.get(reverse("mfa_index"))

        self.assertContains(response, "Authenticator App")
        self.assertContains(response, "card border")

    def test_the_security_key_list_renders(self):
        # Regression: allauth's template does {% load humanize %}, which raised
        # TemplateSyntaxError until django.contrib.humanize was installed.
        self.assertEqual(self.client.get(reverse("mfa_list_webauthn")).status_code, 200)

    def test_the_totp_activate_page_boxes_the_code_and_plates_the_qr(self):
        response = self.client.get(reverse("mfa_activate_totp"))

        self.assertContains(response, "otp otp-lg")
        # The QR is dark-on-transparent: without a white plate it is unscannable on the
        # dark theme.
        self.assertContains(response, "bg-white p-3")
        self.assertContains(response, "font-mono")  # the secret, to be copied by hand

    def test_the_deactivate_button_is_destructive(self):
        enrol_mfa(self.user)

        response = self.client.get(reverse("mfa_index"))

        # allauth tags it "danger" — it must not look like the safe action.
        self.assertContains(response, "btn-error")

    def test_reauthenticating_with_a_code_boxes_the_input(self):
        enrol_mfa(self.user)

        self.assertContains(self.client.get(reverse("mfa_reauthenticate")), "otp otp-lg")


class ActionBarTests(TestCase):
    """A form's action bar is drawn when the actions slot has content.

    Regression: it was keyed on `no_visible_fields`, which allauth sets to say a form has
    no visible *fields* — logout and TOTP deactivate are a bare csrf token plus a button.
    Keying the bar on it hid the button on exactly the pages that are nothing but a button.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = verified_user(email="mfa@example.com", password="pw-secret-123")

    def setUp(self):
        self.client.post(reverse("account_login"), {"login": "mfa@example.com", "password": "pw-secret-123"}, follow=True)

    def test_the_sign_out_page_has_its_button(self):
        response = self.client.get(reverse("account_logout"))

        self.assertContains(response, "Sign Out")
        self.assertContains(response, 'type="submit"')

    def test_the_totp_deactivate_page_has_its_button(self):
        enrol_mfa(self.user)

        response = self.client.get(reverse("mfa_deactivate_totp"))

        self.assertContains(response, "btn-error")
        self.assertContains(response, 'type="submit"')


class SignOutPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = verified_user(email="mfa@example.com", password="pw-secret-123")

    def setUp(self):
        self.client.force_login(self.user)
        self.response = self.client.get(reverse("account_logout"))

    def test_sign_out_and_cancel_sit_side_by_side_with_icons(self):
        html = self.response.content.decode()
        cancel = html[html.index('<a class="btn btn-outline btn-neutral gap-2" href="/">') :]
        sign_out = html[html.index('<button class="btn btn-primary gap-2"') :]

        self.assertIn("<svg", cancel[: cancel.index("</a>")])
        self.assertIn("<svg", sign_out[: sign_out.index("</button>")])

    def test_cancel_does_not_sign_you_out(self):
        # It is a link, not a submit: only the POST logs you out.
        self.client.get("/")

        self.assertTrue(self.client.session.get("_auth_user_id"))

    def test_signing_out_still_works(self):
        self.client.post(reverse("account_logout"))

        self.assertIsNone(self.client.session.get("_auth_user_id"))


class ChangePasswordPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        verified_user(email="mfa@example.com", password="pw-secret-123")

    def setUp(self):
        self.client.post(reverse("account_login"), {"login": "mfa@example.com", "password": "pw-secret-123"}, follow=True)
        self.response = self.client.get(reverse("account_change_password"))

    def test_the_fields_have_no_visible_labels(self):
        # allauth gives each a placeholder, so the label would only repeat it.
        self.assertNotContains(self.response, '<span class="label-text">Current Password</span>')
        self.assertContains(self.response, 'name="oldpassword"')
        self.assertContains(self.response, 'name="password1"')

    def test_the_new_password_keeps_its_help_text(self):
        self.assertContains(self.response, "id_password1_helptext")

    def test_the_current_password_is_set_apart_from_the_new_one(self):
        self.assertContains(self.response, "mt-10")

    def test_forgot_password_is_an_accent_button_and_both_actions_have_icons(self):
        html = self.response.content.decode()
        forgot = html[html.index("btn-accent") :]
        submit = html[html.index('class="btn btn-primary gap-2"') :]

        self.assertIn("<svg", forgot[: forgot.index("</a>")])
        self.assertIn("<svg", submit[: submit.index("</button>")])


class MfaButtonIconTests(TestCase):
    """Every button on the MFA screens carries an icon, and the recovery-code actions are
    ranked: View is primary, Download and Generate are outline. Generate throws away the
    codes you already have, so it must not read as the obvious thing to click."""

    @classmethod
    def setUpTestData(cls):
        cls.user = verified_user(email="mfa@example.com", password="pw-secret-123")

    def setUp(self):
        # Sign in *before* enrolling: a user who already holds a second factor is stopped at
        # the 2FA challenge and never reaches these pages.
        self.client.post(reverse("account_login"), {"login": "mfa@example.com", "password": "pw-secret-123"}, follow=True)
        enrol_mfa(self.user)
        RecoveryCodes.activate(self.user).instance.save()

    def buttons(self, url):
        """Every <a class="btn"> / <button class="btn"> in the page body, minus the navbar."""
        html = self.client.get(url, follow=True).content.decode()
        body = html[html.index("<main") :]
        return re.findall(r'<(?:a|button)[^>]*class="btn[^"]*"[^>]*>(.*?)</(?:a|button)>', body, re.S)

    def test_every_button_on_the_manage_page_has_an_icon(self):
        found = self.buttons(reverse("mfa_index"))

        self.assertTrue(found)
        for button in found:
            self.assertIn("<svg", button)

    def test_download_and_generate_are_outline_buttons(self):
        html = self.client.get(reverse("mfa_index"), follow=True).content.decode()

        self.assertEqual(html.count("btn-outline"), 2)  # Download + Generate, not View

    def test_the_panel_actions_are_spaced_off_the_body_text(self):
        self.assertContains(self.client.get(reverse("mfa_index"), follow=True), "card-actions mt-4")

    def test_every_button_on_the_deactivate_page_has_an_icon(self):
        for button in self.buttons(reverse("mfa_deactivate_totp")):
            self.assertIn("<svg", button)

    def test_every_button_on_the_add_security_key_page_has_an_icon(self):
        for button in self.buttons(reverse("mfa_add_webauthn")):
            self.assertIn("<svg", button)

    def test_the_activate_page_gives_the_code_box_no_visible_label(self):
        Authenticator.objects.filter(user=self.user, type=Authenticator.Type.TOTP).delete()

        response = self.client.get(reverse("mfa_activate_totp"), follow=True)

        self.assertContains(response, "otp otp-lg")
        self.assertNotContains(response, '<span class="label-text">Code</span>')


class AccountAdapterClientIPTests(TestCase):
    """allauth's rate limits key on RosterChiefAccountAdapter.get_client_ip:
    the proxy-supplied X-Real-IP, not the proxy's own REMOTE_ADDR."""

    def test_uses_the_proxy_supplied_client_ip(self):
        request = RequestFactory().get("/", HTTP_X_REAL_IP="203.0.113.7", REMOTE_ADDR="172.18.0.5")

        self.assertEqual(RosterChiefAccountAdapter(request).get_client_ip(request), "203.0.113.7")

    def test_falls_back_to_remote_addr_without_the_header(self):
        request = RequestFactory().get("/", REMOTE_ADDR="198.51.100.9")

        self.assertEqual(RosterChiefAccountAdapter(request).get_client_ip(request), "198.51.100.9")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailVerificationTests(TestCase):
    """ACCOUNT_EMAIL_VERIFICATION = "mandatory" + ACCOUNT_CHANGE_EMAIL: an address
    has to be confirmed before it signs anyone in, and a newly added one only
    becomes the account's email once confirmed. Staff grants
    (controlpanel.services.accounts) bind to an account through its email, so an
    account must never be able to claim an address it can't receive mail at."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def log_in(self, email="member@example.com", password="pw-secret-123"):
        return self.client.post(reverse("account_login"), {"login": email, "password": password})

    def signed_in(self):
        return "_auth_user_id" in self.client.session

    def confirm_link_sent_to(self, email):
        message = next(m for m in reversed(mail.outbox) if email in m.to)
        return re.search(r"https?://[^\s]+/accounts/confirm-email/([^\s/]+)/", message.body).group(0)

    def test_an_unconfirmed_address_cannot_sign_in(self):
        User.objects.create_user(email="member@example.com", password="pw-secret-123")

        self.log_in()

        self.assertFalse(self.signed_in())
        self.assertEqual([m.to for m in mail.outbox], [["member@example.com"]])

    def test_following_the_confirmation_link_lets_them_sign_in(self):
        user = User.objects.create_user(email="member@example.com", password="pw-secret-123")
        self.log_in()

        self.client.post(urlparse(self.confirm_link_sent_to("member@example.com")).path)
        self.log_in()

        self.assertTrue(self.signed_in())
        self.assertTrue(EmailAddress.objects.get(user=user, email="member@example.com").verified)

    def test_an_added_address_cannot_be_made_primary_before_it_is_confirmed(self):
        user = verified_user("member@example.com", "pw-secret-123")
        self.log_in()
        self.assertTrue(self.signed_in())

        self.client.post(reverse("account_email"), {"action_add": "", "email": "coach@club.example"})
        self.client.post(reverse("account_email"), {"action_primary": "", "email": "coach@club.example"})

        user.refresh_from_db()
        self.assertEqual(user.email, "member@example.com")
        self.assertFalse(EmailAddress.objects.get(user=user, email="coach@club.example").primary)

        # ...so a grant for that address doesn't land on this account.
        club = Club.objects.create(name="Ajax United", slug="ajax-united-verify")
        role = grant_club_admin(club, "coach@club.example", "Co", "Ach")
        self.assertNotEqual(role.member.user_id, user.pk)

    def test_confirming_an_added_address_makes_it_the_account_email(self):
        user = verified_user("member@example.com", "pw-secret-123")
        self.log_in()
        self.client.post(reverse("account_email"), {"action_add": "", "email": "new@example.com"})

        self.client.post(urlparse(self.confirm_link_sent_to("new@example.com")).path)

        user.refresh_from_db()
        self.assertEqual(user.email, "new@example.com")
        self.assertEqual(list(EmailAddress.objects.filter(user=user).values_list("email", "verified", "primary")), [("new@example.com", True, True)])

    def test_an_invited_admin_sets_a_password_then_confirms_their_address_on_first_sign_in(self):
        # grant_club_admin creates the account without a password or a confirmed
        # address. allauth's password reset sets the password but does not mark
        # the address verified, so the first sign-in asks for confirmation too.
        club = Club.objects.create(name="Ajax United", slug="ajax-united-invite")
        grant_club_admin(club, "invitee@example.com", "In", "Vitee")

        self.client.post(reverse("account_reset_password"), {"email": "invitee@example.com"})
        reset_url = urlparse(re.search(r"https?://[^\s]+/accounts/password/reset/key/[^\s]+", mail.outbox[-1].body).group(0)).path
        form_url = self.client.get(reset_url)["Location"]
        self.client.post(form_url, {"password1": "a-New-pw-4567", "password2": "a-New-pw-4567"})
        self.log_in("invitee@example.com", "a-New-pw-4567")
        self.assertFalse(self.signed_in())

        self.client.post(urlparse(self.confirm_link_sent_to("invitee@example.com")).path)
        self.log_in("invitee@example.com", "a-New-pw-4567")

        self.assertTrue(self.signed_in())


class VerifyExistingAccountEmailsMigrationTests(TestCase):
    """authentication/migrations/0005_verify_existing_account_emails: accounts
    created before verification became mandatory keep being able to sign in --
    their current login address is marked verified, nothing else is."""

    def run_migration(self):
        from django.apps import apps

        module = importlib.import_module("authentication.migrations.0005_verify_existing_account_emails")
        module.verify_existing_account_emails(apps, None)

    def test_creates_a_verified_primary_row_for_the_login_address(self):
        user = User.objects.create_user(email="Member@example.com", password="x")

        self.run_migration()

        self.assertEqual(list(EmailAddress.objects.filter(user=user).values_list("email", "verified", "primary")), [("member@example.com", True, True)])

    def test_verifies_an_existing_row_and_moves_primary_onto_it(self):
        user = User.objects.create_user(email="member@example.com", password="x")
        EmailAddress.objects.create(user=user, email="member@example.com", verified=False, primary=False)
        EmailAddress.objects.create(user=user, email="other@example.com", verified=False, primary=True)

        self.run_migration()

        rows = {email: (verified, primary) for email, verified, primary in EmailAddress.objects.filter(user=user).values_list("email", "verified", "primary")}
        self.assertEqual(rows, {"member@example.com": (True, True), "other@example.com": (False, False)})

    def test_leaves_an_address_another_account_already_verified_alone(self):
        owner = User.objects.create_user(email="owner@example.com", password="x")
        EmailAddress.objects.create(user=owner, email="shared@example.com", verified=True, primary=False)
        user = User.objects.create_user(email="shared@example.com", password="x")

        self.run_migration()

        self.assertFalse(EmailAddress.objects.filter(user=user, verified=True).exists())

    def test_is_idempotent(self):
        user = User.objects.create_user(email="member@example.com", password="x")

        self.run_migration()
        self.run_migration()

        self.assertEqual(EmailAddress.objects.filter(user=user).count(), 1)


class MfaSecretEncryptionTests(TestCase):
    """authentication/crypto.py via RosterChiefMFAAdapter.encrypt/decrypt: TOTP
    secrets and recovery-code seeds are stored Fernet-encrypted, legacy plaintext
    values keep working, and TOTP sign-in still verifies codes end to end."""

    SECRET = "JBSWY3DPEHPK3PXP"

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def current_code(self, secret=SECRET):
        from allauth.mfa.totp.internal.auth import format_hotp_value, hotp_value, yield_hotp_counters_from_time

        counters = list(yield_hotp_counters_from_time())
        return format_hotp_value(hotp_value(secret, counters[len(counters) // 2]))

    def test_round_trip_and_prefix(self):
        from . import crypto

        stored = RosterChiefMFAAdapter().encrypt(self.SECRET)

        self.assertTrue(stored.startswith("fernet:"))
        self.assertNotIn(self.SECRET, stored)
        self.assertEqual(RosterChiefMFAAdapter().decrypt(stored), self.SECRET)
        self.assertTrue(crypto.is_encrypted(stored))

    def test_legacy_plaintext_is_returned_unchanged(self):
        self.assertEqual(RosterChiefMFAAdapter().decrypt(self.SECRET), self.SECRET)

    def test_a_missing_key_is_a_configuration_error(self):
        from django.core.exceptions import ImproperlyConfigured

        from .checks import mfa_encryption_key_check

        with override_settings(MFA_ENCRYPTION_KEY=""):
            with self.assertRaises(ImproperlyConfigured):
                RosterChiefMFAAdapter().encrypt(self.SECRET)
            self.assertEqual([error.id for error in mfa_encryption_key_check(None)], ["authentication.E001"])
        self.assertEqual(mfa_encryption_key_check(None), [])

    def test_rotated_keys_still_decrypt(self):
        from cryptography.fernet import Fernet

        old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        with override_settings(MFA_ENCRYPTION_KEY=old_key):
            stored = RosterChiefMFAAdapter().encrypt(self.SECRET)
        with override_settings(MFA_ENCRYPTION_KEY=f"{new_key},{old_key}"):
            self.assertEqual(RosterChiefMFAAdapter().decrypt(stored), self.SECRET)

    def test_totp_activation_stores_the_secret_encrypted(self):
        from allauth.mfa.totp.internal.auth import TOTP

        user = User.objects.create_user(email="member@example.com", password="x")
        totp = TOTP.activate(user, self.SECRET)

        stored = Authenticator.objects.get(pk=totp.instance.pk).data["secret"]
        self.assertTrue(stored.startswith("fernet:"))
        self.assertTrue(TOTP(Authenticator.objects.get(pk=totp.instance.pk)).validate_code(self.current_code()))

    def test_recovery_codes_seed_is_encrypted_and_codes_still_work(self):
        user = User.objects.create_user(email="member@example.com", password="x")
        codes = RecoveryCodes.activate(user)
        instance = Authenticator.objects.get(pk=codes.instance.pk)

        self.assertTrue(instance.data["seed"].startswith("fernet:"))
        unused = RecoveryCodes(instance).get_unused_codes()
        self.assertEqual(len(unused), 10)
        self.assertTrue(RecoveryCodes(instance).validate_code(unused[0]))

    def sign_in_with_totp(self, stored_secret):
        user = verified_user("member@example.com", "pw-secret-123")
        Authenticator.objects.create(user=user, type=Authenticator.Type.TOTP, data={"secret": stored_secret})
        response = self.client.post(reverse("account_login"), {"login": "member@example.com", "password": "pw-secret-123"})
        self.assertRedirects(response, reverse("mfa_authenticate"), fetch_redirect_response=False)
        self.client.post(reverse("mfa_authenticate"), {"code": self.current_code()})
        return "_auth_user_id" in self.client.session

    def test_sign_in_with_an_encrypted_totp_secret(self):
        self.assertTrue(self.sign_in_with_totp(RosterChiefMFAAdapter().encrypt(self.SECRET)))

    def test_sign_in_with_a_legacy_plaintext_totp_secret(self):
        self.assertTrue(self.sign_in_with_totp(self.SECRET))


class EncryptMfaSecretsMigrationTests(TestCase):
    """authentication/migrations/0006_encrypt_mfa_secrets encrypts existing
    plaintext secrets, skips already-encrypted ones, and reverses cleanly."""

    def setUp(self):
        from django.apps import apps

        self.apps = apps
        self.module = importlib.import_module("authentication.migrations.0006_encrypt_mfa_secrets")
        user = User.objects.create_user(email="member@example.com", password="x")
        self.totp = Authenticator.objects.create(user=user, type=Authenticator.Type.TOTP, data={"secret": "JBSWY3DPEHPK3PXP"})
        self.codes = Authenticator.objects.create(user=user, type=Authenticator.Type.RECOVERY_CODES, data={"seed": "abc123", "used_mask": 3, "migrated_codes": ["11111111", "22222222"]})

    def test_encrypts_plaintext_secrets(self):
        self.module.encrypt_mfa_secrets(self.apps, None)

        self.totp.refresh_from_db()
        self.codes.refresh_from_db()
        self.assertTrue(self.totp.data["secret"].startswith("fernet:"))
        self.assertTrue(self.codes.data["seed"].startswith("fernet:"))
        self.assertTrue(all(code.startswith("fernet:") for code in self.codes.data["migrated_codes"]))
        self.assertEqual(self.codes.data["used_mask"], 3)
        adapter = RosterChiefMFAAdapter()
        self.assertEqual(adapter.decrypt(self.totp.data["secret"]), "JBSWY3DPEHPK3PXP")
        self.assertEqual([adapter.decrypt(code) for code in self.codes.data["migrated_codes"]], ["11111111", "22222222"])

    def test_is_idempotent(self):
        self.module.encrypt_mfa_secrets(self.apps, None)
        self.totp.refresh_from_db()
        first = self.totp.data["secret"]

        self.module.encrypt_mfa_secrets(self.apps, None)

        self.totp.refresh_from_db()
        self.assertEqual(self.totp.data["secret"], first)

    def test_reverse_decrypts(self):
        self.module.encrypt_mfa_secrets(self.apps, None)
        self.module.decrypt_mfa_secrets(self.apps, None)

        self.totp.refresh_from_db()
        self.codes.refresh_from_db()
        self.assertEqual(self.totp.data["secret"], "JBSWY3DPEHPK3PXP")
        self.assertEqual(self.codes.data["seed"], "abc123")
