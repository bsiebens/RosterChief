from allauth.account.forms import ResetPasswordForm
from allauth.account.models import EmailAddress
from allauth.core import context
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from django_tenants.utils import get_public_schema_name, schema_context

from authorization.models import Role
from authorization.services import assign_role
from features.catalog import DEFAULT_PLAN
from features.entitlements import resolve_entitlements
from features.models import Plan
from features.services import subscribe
from members.models import Member

from .models import Club, Domain


class AccountError(Exception):
    pass


class UnverifiedEmailError(AccountError):
    """The address belongs to an account whose email was never verified, so it must not inherit rights."""


def resolve_account(email: str, *, first_name: str = "", last_name: str = ""):
    """Find or create the account for ``email`` before giving it rights. Returns ``(user, created)``.

    An existing account is only reused if that exact address is verified. A new account is login-less; the caller
    sends the password setup email.
    """
    email = email.strip().lower()
    with schema_context(get_public_schema_name()):
        user = get_user_model().objects.filter(email__iexact=email).first()
        if user is not None:
            if not EmailAddress.objects.filter(user=user, email__iexact=email, verified=True).exists():
                raise UnverifiedEmailError("That account's email address has not been verified.")
            return user, False
        if not (first_name and last_name):
            raise AccountError("First and last name are required for a new account.")
        return create_account(email, first_name=first_name, last_name=last_name), True


def mark_email_verified(user) -> None:
    """Mark the user's login email as verified and primary, both on the user and in allauth."""
    with schema_context(get_public_schema_name()):
        if not user.is_verified:
            user.is_verified = True
            user.save(update_fields=["is_verified"])
        EmailAddress.objects.update_or_create(user=user, email=user.email, defaults={"primary": True, "verified": True})


@transaction.atomic
def create_account(email: str, first_name: str = "", last_name: str = ""):
    """Create a login-less account (unusable password). The user sets a password via the reset flow.

    Marking the address verified is safe: the only way to set a password is the emailed reset link.
    """
    with schema_context(get_public_schema_name()):
        user = get_user_model().objects.create_user(email=email, first_name=first_name, last_name=last_name)
        mark_email_verified(user)
    return user


# Subdomains that must never be handed to a club.
RESERVED_SLUGS = frozenset({"www", "admin", "api", "app", "accounts", "login", "mail", "media", "public", "static"})
SLUG_MAX_LENGTH = 40  # keeps "club_" + slug well inside PostgreSQL's 63 character identifier limit
SCHEMA_PREFIX = "club_"


def schema_name_for(slug: str) -> str:
    """Schema names are derived from the slug: ``sharks-mechelen`` -> ``club_sharks_mechelen``.

    The prefix keeps names clear of PostgreSQL's reserved ``pg_`` prefix, ``public`` and leading digits.
    """
    return SCHEMA_PREFIX + slug.replace("-", "_")


def generate_club_slug(name: str) -> str:
    """A slug that is a valid, unused subdomain label and schema name."""
    base = slugify(name)[:SLUG_MAX_LENGTH].strip("-") or "club"
    slug, n = base, 1
    while slug in RESERVED_SLUGS or _slug_taken(slug):
        n += 1
        suffix = f"-{n}"
        slug = base[: SLUG_MAX_LENGTH - len(suffix)].rstrip("-") + suffix
    return slug


def _slug_taken(slug: str) -> bool:
    return Club.objects.filter(slug=slug).exists() or Club.objects.filter(schema_name__iexact=schema_name_for(slug)).exists() or Domain.objects.filter(domain__iexact=club_domain(slug)).exists()


def club_domain(slug: str) -> str:
    return f"{slug}.{settings.ROSTERCHIEF_BASE_DOMAIN}"


@transaction.atomic
def provision_club(club: Club) -> Club:
    """Provision an unsaved ``Club`` that has ``name`` and ``owner`` (and optionally ``sport``) set.

    Generates the slug and schema name, creates the schema (and, through the post_schema_sync signal, the
    default roles and the owner's Owner role), puts the owner on the roster and registers ``<slug>.<base domain>`` as the primary domain.
    """
    if not club._state.adding:
        raise ValueError("Club is already provisioned.")
    with schema_context(get_public_schema_name()):
        club.slug = generate_club_slug(club.name)
        club.schema_name = schema_name_for(club.slug)
        club.save()
        Domain.objects.create(domain=club_domain(club.slug), tenant=club, is_primary=True)
    plan = Plan.objects.filter(code=DEFAULT_PLAN).first()
    if plan is not None:
        subscribe(club, plan, reason="Default plan")
    ensure_member_for_user(club, club.owner)
    _invalidate_dashboard()
    return club


def create_club(name: str, owner, *, sport=None) -> Club:
    return provision_club(Club(name=name, owner=owner, sport=sport))


def ensure_member_for_user(club: Club, user) -> Member:
    """Make sure ``user`` has a roster entry in ``club``, copying name and email from the user. Idempotent."""
    with schema_context(club.schema_name):
        member, _ = Member.objects.get_or_create(
            user=user,
            defaults={"first_name": user.first_name, "last_name": user.last_name, "email": user.email},
        )
    return member


@transaction.atomic
def create_member(club: Club, first_name: str, last_name: str, email: str = "", *, features=None) -> Member:
    """Add a person to the club's roster without a login.

    Enforces the club's ``max_members`` limit and raises ``features.entitlements.LimitExceeded`` when it is reached.
    Pass ``features`` (e.g. ``request.features``) to honour the request's entitlements, including the platform
    owner's bypass; otherwise they are resolved for the club.
    """
    with schema_context(get_public_schema_name()):
        # Serialise member creation per club so two concurrent requests cannot both slip under the limit.
        Club.objects.select_for_update().get(pk=club.pk)
    with schema_context(club.schema_name):
        (features or resolve_entitlements(club)).check_limit("max_members", Member.objects.count())
        return Member.objects.create(first_name=first_name, last_name=last_name, email=email)


@transaction.atomic
def grant_login(club: Club, member: Member, *, role_slug: str = "member"):
    """Give an existing member access to the club, using ``member.email`` as the login.

    Returns ``(user, user_created)``. Only send the password setup email when ``user_created`` is True;
    an existing user already has credentials and just gains access to another club.
    """
    if not member.email:
        raise ValueError("Member needs an email address to receive a login.")
    if member.user_id:
        raise ValueError("Member already has a login.")

    with schema_context(get_public_schema_name()):
        user = get_user_model().objects.filter(email__iexact=member.email).first()
    user_created = user is None
    if user_created:
        user = create_account(member.email, member.first_name, member.last_name)

    club.add_user(user)  # public: user.tenants link; club schema: UserTenantPermissions (raises ExistsError if already in)

    with schema_context(club.schema_name):
        assign_role(user, Role.objects.get(slug=role_slug))
        member.user = user
        member.save(update_fields=["user", "modified"])
    return user, user_created


@transaction.atomic
def create_member_for_club(club: Club, email: str, first_name: str, last_name: str, *, role_slug: str = "member", features=None):
    """Create a member and give them a login in one step. Returns ``(member, user, user_created)``."""
    member = create_member(club, first_name, last_name, email, features=features)
    user, user_created = grant_login(club, member, role_slug=role_slug)
    return member, user, user_created


def send_password_setup_email(request, user) -> None:
    """Email the user a password reset link (used as the account invite)."""
    form = ResetPasswordForm(data={"email": user.email})
    if not form.is_valid():
        raise ValueError(form.errors.as_text())
    with context.request_context(request):
        form.save(request)


def _invalidate_dashboard() -> None:
    from controlpanel import stats

    stats.invalidate()


def archive_club(club: Club) -> Club:
    """Archive a club: its domains stop resolving (see ArchivedClubMiddleware); all data is kept."""
    if club.schema_name == get_public_schema_name():
        raise ValueError("The platform itself cannot be archived.")
    if club.archived_at is None:
        club.archived_at = timezone.now()
        club.save(update_fields=["archived_at", "modified"])
        _invalidate_dashboard()
    return club


def restore_club(club: Club) -> Club:
    if club.archived_at is not None:
        club.archived_at = None
        club.save(update_fields=["archived_at", "modified"])
        _invalidate_dashboard()
    return club


def delete_club(club: Club) -> None:
    """Permanently delete a club and its schema. People (users) are kept; only the club's own rows go."""
    if club.schema_name == get_public_schema_name():
        raise ValueError("The platform itself cannot be deleted.")
    club.delete(force_drop=True)
    _invalidate_dashboard()


class ClubAdminError(Exception):
    pass


def club_admins(club: Club) -> list:
    """Users holding the Owner role in ``club``."""
    from authorization.catalog import OWNER_SLUG

    with schema_context(club.schema_name):
        group = Role.objects.get(slug=OWNER_SLUG).group
        return [perms.profile for perms in group.user_set.select_related("profile").order_by("profile__email")]


@transaction.atomic
def grant_club_admin(club: Club, user) -> None:
    """Make ``user`` an administrator of ``club``: grant access if they have none, put them on the roster, give them Owner."""
    from authorization.catalog import OWNER_SLUG

    if not club.user_set.filter(pk=user.pk).exists():
        club.add_user(user)
    ensure_member_for_user(club, user)
    with schema_context(club.schema_name):
        assign_role(user, Role.objects.get(slug=OWNER_SLUG))


@transaction.atomic
def revoke_club_admin(club: Club, user) -> None:
    """Take the Owner role from ``user`` (they stay a member). The club's owner and its last Owner cannot be removed."""
    from authorization.catalog import OWNER_SLUG
    from authorization.services import remove_role

    if user.pk == club.owner_id:
        raise ClubAdminError("The club's owner cannot be removed as an administrator. Transfer ownership first.")
    with schema_context(club.schema_name):
        remove_role(user, Role.objects.get(slug=OWNER_SLUG))  # raises ProtectedRoleError for the last Owner
        if not user.tenant_perms.groups.exists():
            assign_role(user, Role.objects.get(slug="member"))
