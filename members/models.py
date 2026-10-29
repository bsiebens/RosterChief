import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models.fields.files import ImageFieldFile
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from phonenumber_field.modelfields import PhoneNumberField

from rosterchief.base import ClubScopedModel, UUIDModel
from rosterchief.storage import get_private_storage


def member_photo_path(instance, filename):
    # Keyed off the member's own id, not e.g. a club slug -- Member is the one
    # global model in this codebase (shared across every club a person
    # belongs to), so there's no single club to scope the path by. Same
    # "stable id, not a name/slug" reasoning as news_photo_path/team_photo_path.
    return f"members/{instance.pk}/{filename}"


class MemberPhotoFieldFile(ImageFieldFile):
    """A member photo lives in private storage, which has no URL of its own
    (rosterchief.storage.PrivateStorage). ``.url`` answers with the authorising
    view instead (``Member.photo_url``), so anything that still asks a photo for
    its URL -- a ClearableFileInput's "Currently:" link, the public roster API --
    gets a link that goes through the access check rather than an exception."""

    @property
    def url(self):
        self._require_file()
        return self.instance.photo_url_for(self.field.name)


class MemberPhotoField(models.ImageField):
    attr_class = MemberPhotoFieldFile


class Family(ClubScopedModel):
    """Club-scoped: a household's registration is with one club, and a person
    who belongs to more than one club (e.g. a coach who's also a parent
    elsewhere) gets a separate Family row per club rather than one shared
    row leaking one club's household composition into another's admin view."""

    name = models.CharField(_("name"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("family")
        verbose_name_plural = _("families")
        ordering = ["name"]

    def __str__(self):
        if self.name:
            return self.name
        surnames = sorted({last_name for last_name in self.memberships.values_list("member__last_name", flat=True) if last_name})
        if surnames:
            return _("%(surnames)s") % {"surnames": " / ".join(surnames)}
        return _("Family %(id)s") % {"id": str(self.pk)[:8]}

    @property
    def guardians(self):
        return Member.objects.filter(
            family_memberships__family=self,
            family_memberships__role__in=[FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN],
        )

    @property
    def children(self):
        return Member.objects.filter(
            family_memberships__family=self,
            family_memberships__role=FamilyMembership.FamilyRole.CHILD,
        )


class Member(UUIDModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="member", null=True, blank=True, verbose_name=_("user"))

    first_name = models.CharField(_("first name"), max_length=150)
    last_name = models.CharField(_("last name"), max_length=150)

    date_of_birth = models.DateField(_("date of birth"), null=True, blank=True)

    email = models.EmailField(_("email"), blank=True)
    phone = PhoneNumberField(_("phone number"), null=True, blank=True)
    emergency_phone = PhoneNumberField(_("emergency phone number"), null=True, blank=True)

    #: The official player picture. Private storage: readable only through
    #: members.views.MemberPhotoView, which applies
    #: members.services.photos.can_view_member_photo. Only changeable in the
    #: management portal by whoever may edit member data; the only picture
    #: the public API can ever return (see ``public_photo``).
    player_photo = MemberPhotoField(_("player picture"), upload_to=member_photo_path, storage=get_private_storage, blank=True)
    #: Self-service picture, set by the member (or their guardian) in the
    #: app/PWA and used inside the club's apps in preference to the player
    #: picture. Never exposed publicly.
    profile_photo = MemberPhotoField(_("profile picture"), upload_to=member_photo_path, storage=get_private_storage, blank=True)
    #: Gates the *public* surfaces (a club's public website/API roster): once
    #: on, members.views.MemberPhotoView serves the photo to anyone. Without
    #: it, only staff who may see this member, the member themselves and their
    #: parents/guardians can (members.services.photos). Off by default: a photo uploaded for
    #: in-app use (e.g. a parent picking their own kid out of a roster list)
    #: shouldn't become world-visible without a separate, explicit opt-in --
    #: this matters most for underage players.
    photo_public_consent = models.BooleanField(_("show the player picture on the public website/API"), default=False)

    #: Preferred language (one of settings.LANGUAGES), set from the app's Language picker. Blank = no
    #: preference: the browser/club default applies and push falls back to the stored English text.
    language = models.CharField(_("language"), max_length=8, blank=True, choices=settings.LANGUAGES)

    class Meta:
        verbose_name = _("member")
        verbose_name_plural = _("members")
        ordering = ["last_name", "first_name"]
        indexes = [models.Index(fields=["last_name", "first_name"])]

    def __str__(self):
        return self.get_full_name()

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name

    @property
    def photo(self):
        """The picture the club's apps show: the profile picture, else the player picture."""
        return self.profile_photo or self.player_photo

    def photo_url_for(self, field_name):
        """The authorising view's URL for one of this member's pictures
        (``player_photo``/``profile_photo``), or None without one. The ``v``
        parameter changes whenever the stored file does, so a browser's private
        cache never shows a replaced picture."""
        f = getattr(self, field_name)
        if not f:
            return None
        version = hashlib.sha256(f.name.encode()).hexdigest()[:12]
        route = {"player_photo": "members:player_photo", "profile_photo": "members:profile_photo"}[field_name]
        return f"{reverse(route, args=[self.pk])}?v={version}"

    @property
    def player_photo_url(self):
        return self.photo_url_for("player_photo")

    @property
    def profile_photo_url(self):
        return self.photo_url_for("profile_photo")

    @property
    def photo_url(self):
        """URL of the effective picture (profile, else player)."""
        f = self.photo
        if not f:
            return None
        version = hashlib.sha256(f.name.encode()).hexdigest()[:12]
        return f"{reverse('members:photo', args=[self.pk])}?v={version}"

    @property
    def public_photo(self):
        """The *player* picture, but only once ``photo_public_consent`` says it
        can leave the club's own app/staff views -- the one gate every public
        surface (api.public.routers.teams's roster endpoints, a club's public website) must
        go through, so consent can't be forgotten at a new call site. The
        profile picture is never public."""
        return self.player_photo if self.player_photo and self.photo_public_consent else None

    @property
    def contact_email(self):
        """Best email to reach this member: own contact email, else login email."""
        return self.email or (self.user.email if self.user_id else "")

    def guardians(self, club):
        """This member's parents/guardians, scoped to `club` -- a Member row
        is global (shared across every club a person belongs to), but Family
        is per-club, so without this filter a member in two clubs would leak
        one club's guardians into the other's."""
        return Member.objects.filter(
            family_memberships__role__in=[FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN],
            family_memberships__family__club=club,
            family_memberships__family__memberships__member=self,
            family_memberships__family__memberships__role=FamilyMembership.FamilyRole.CHILD,
        ).distinct()

    def family_members(self, club):
        """Everyone sharing any Family with this member within `club`, any role,
        either direction -- not just this member's own children (see
        ``guardians`` for the parent-scoped, one-directional version).
        Excludes self. Used e.g. to decide whose vouchers are relevant to an
        order this member placed (management.forms.AddPaymentForm)."""
        return Member.objects.filter(family_memberships__family__club=club, family_memberships__family__memberships__member=self).exclude(pk=self.pk).distinct()


class FamilyMembership(models.Model):
    class FamilyRole(models.TextChoices):
        PARENT = "parent", _("parent")
        CHILD = "child", _("child")
        GUARDIAN = "guardian", _("guardian")
        OTHER = "other", _("other")

    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="memberships", verbose_name=_("family"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="family_memberships", verbose_name=_("member"))
    role = models.CharField(_("role"), max_length=255, choices=FamilyRole.choices, default=FamilyRole.PARENT)

    class Meta:
        verbose_name = _("family membership")
        verbose_name_plural = _("family memberships")
        ordering = ["family", "role", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["family", "member"], name="unique_member_per_family"),
        ]

    def __str__(self):
        return f"{self.family} - {self.member} ({self.get_role_display()})"


def _generate_invite_token() -> str:
    return secrets.token_urlsafe(32)


def _default_invite_expiry():
    return timezone.now() + FamilyInvite.VALID_FOR


class FamilyInvite(ClubScopedModel):
    """A club's request for an existing account holder to join one of its
    families as a parent/guardian (members.services.invites). Member is
    global, so staff typing an email that already has an account never links
    that person directly: nothing changes until the account holder signs in
    and accepts. ``token`` is the single-use credential in the emailed link
    (same secrets.token_urlsafe(32) approach as RegistrationBatch.status_token),
    only honoured for this club, before ``expires_at``, and for a signed-in
    user whose email matches ``email``."""

    VALID_FOR = timedelta(days=14)

    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="invites", verbose_name=_("family"))
    email = models.EmailField(_("email"))
    first_name = models.CharField(_("first name"), max_length=150, blank=True)
    last_name = models.CharField(_("last name"), max_length=150, blank=True)
    parent_is_member = models.BooleanField(_("also a member"), default=False)
    token = models.CharField(_("token"), max_length=64, unique=True, editable=False, default=_generate_invite_token)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="+", null=True, blank=True, verbose_name=_("invited by"))
    expires_at = models.DateTimeField(_("expires at"), default=_default_invite_expiry)
    accepted_at = models.DateTimeField(_("accepted at"), null=True, blank=True)
    accepted_member = models.ForeignKey(Member, on_delete=models.SET_NULL, related_name="+", null=True, blank=True, verbose_name=_("accepted by"))

    class Meta:
        verbose_name = _("family invite")
        verbose_name_plural = _("family invites")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.email} -> {self.family}"

    @property
    def is_pending(self):
        return self.accepted_at is None and self.expires_at > timezone.now()


class Group(ClubScopedModel):
    """An arbitrary named collection of members -- deliberately generic, not
    team-shaped and not aware of any specific use: "all coaches", "all team
    managers", an ad-hoc committee. A Team's roster is a separate, more
    specific concept (teams.TeamMembership); nothing here assumes team
    semantics. Referee eligibility (who can ref which team) is a member-level
    fact (teams.RefereeProfile), not a Group concern -- Group carries no
    referee-specific knowledge at all."""

    name = models.CharField(_("name"), max_length=255)

    class Meta:
        verbose_name = _("group")
        verbose_name_plural = _("groups")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["club", "name"], name="unique_group_name_per_club"),
        ]

    def __str__(self):
        return self.name


class GroupMembership(UUIDModel):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="memberships", verbose_name=_("group"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="group_memberships", verbose_name=_("member"))

    class Meta:
        verbose_name = _("group membership")
        verbose_name_plural = _("group memberships")
        ordering = ["group", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["group", "member"], name="unique_member_per_group"),
        ]

    def __str__(self):
        return f"{self.group} - {self.member}"
