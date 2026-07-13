import uuid
from urllib.parse import parse_qs, urlparse

from allauth.core import context
from allauth.mfa.models import Authenticator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from club.models import Club, ClubRole
from members.models import Member

from .adapters import RosterChiefMFAAdapter, webauthn_rp_id
from .middleware import RequireMFAMiddleware, mfa_required_for

User = get_user_model()


def enrol_mfa(user):
    """Give ``user`` a second factor (enough for is_mfa_enabled)."""
    return Authenticator.objects.create(user=user, type=Authenticator.Type.TOTP, data={"secret": "JBSWY3DPEHPK3PXP"})


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
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")

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

    def test_allauth_login_page_loads(self):
        self.assertEqual(self.client.get(reverse("account_login")).status_code, 200)


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

    def test_the_signup_form_renders_its_fields(self):
        self.assertContains(self.client.get(reverse("account_signup")), 'name="password1"')


class TwoFactorPageTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(email="mfa@example.com", password="pw-secret-123")
        enrol_mfa(user)
        # Password accepted, second factor still owed: this is the 2FA challenge page.
        self.response = self.client.post(reverse("account_login"), {"login": "mfa@example.com", "password": "pw-secret-123"}, follow=True)

    def test_the_code_field_renders_as_an_otp_input(self):
        self.assertContains(self.response, 'name="code"')
        self.assertContains(self.response, "otp otp-lg")

    def test_cancel_sits_beside_sign_in_and_is_not_primary(self):
        self.assertContains(self.response, '<button class="btn gap-2" type="submit" form="logout-from-stage">')
        self.assertContains(self.response, '<button class="btn btn-primary gap-2" type="submit">')

    def test_cancel_has_a_form_to_submit(self):
        self.assertContains(self.response, 'id="logout-from-stage"')

    def test_the_security_key_button_is_an_accent_button_with_a_working_form(self):
        self.assertContains(self.response, "btn btn-accent")
        self.assertContains(self.response, 'form="webauthn_form"')
        # The id lives on the form element — without it the button submits nothing.
        self.assertContains(self.response, 'id="webauthn_form"')
        self.assertContains(self.response, "allauth.webauthn.forms.authenticateForm")
