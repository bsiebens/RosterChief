import hashlib

from django.conf import settings
from django.db import models
from django.db.models.fields.files import ImageFieldFile
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from phonenumber_field.modelfields import PhoneNumberField

from rosterchief.base import UUIDModel
from rosterchief.storage import get_private_storage


def member_photo_path(instance, filename):
    return f"members/{instance.pk}/{filename}"


class MemberPhotoFieldFile(ImageFieldFile):
    @property
    def url(self):
        self._require_file()
        return self.instance.photo_url_for(self.field.name)


class MemberPhotoField(models.ImageField):
    attr_class = MemberPhotoFieldFile


class Member(UUIDModel):
    """A person on a club's roster. Lives in the club schema. A login (``user``) is optional."""

    class MemberType(models.TextChoices):
        """What kind of member someone is. Only one exists for now; add values here as the club types are defined."""

        MEMBER = "member", _("Member")
        STAFF = "staff", _("Staff")
        GUARDIAN = "guardian", _("guardian")

    user = models.OneToOneField(settings.AUTH_USER_MODEL, verbose_name=_("user"), null=True, blank=True, on_delete=models.DO_NOTHING, related_name="+")

    first_name = models.CharField(_("first_name"), max_length=75)
    last_name = models.CharField(_("last_name"), max_length=75)
    email = models.EmailField(_("email"), blank=True, help_text=_("Contact address. Also used as the login email when access is granted."))

    phone = PhoneNumberField(_("phone number"), null=True, blank=True)
    emergency_phone = PhoneNumberField(_("emergency phone number"), null=True, blank=True)

    player_photo = MemberPhotoField(_("player picture"), upload_to=member_photo_path, storage=get_private_storage, blank=True)
    profile_photo = MemberPhotoField(_("profile picture"), upload_to=member_photo_path, storage=get_private_storage, blank=True)
    photo_public_consent = models.BooleanField(_("show the player picture on the public website/API"), default=False)

    member_type = models.CharField(_("member type"), max_length=30, choices=MemberType.choices, default=MemberType.MEMBER, db_index=True)

    class Meta:
        verbose_name = _("member")
        verbose_name_plural = _("members")
        ordering = ["last_name", "first_name"]
        indexes = [models.Index(fields=["last_name", "first_name"])]

    def __str__(self):
        return self.get_full_name

    @property
    def photo(self):
        """The picture the club apps show: the profile picture, else the player picture"""
        return self.profile_photo or self.player_photo

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
        surface (teams.api's roster endpoints, a club's public website) must
        go through, so consent can't be forgotten at a new call site. The
        profile picture is never public."""
        return self.player_photo if self.player_photo and self.photo_public_consent else None

    @property
    def contact_email(self):
        """Best email to reach this member: own contact email, else login email."""
        return self.email or (self.user.email if self.user_id else "")

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name

    def photo_url_for(self, field_name: str):
        f = getattr(self, field_name)

        if not f:
            return None

        version = hashlib.sha256(f.name.encode()).hexdigest()[:12]
        route = {"player_photo": "members:player_photo", "profile_photo": "members:profile_photo"}[field_name]
        return f"{reverse(route, args=[self.pk])}?v={version}"


class Family(UUIDModel):
    name = models.CharField(_("name"), max_length=250, blank=True)

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


class FamilyMembership(UUIDModel):
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


class Group(UUIDModel):
    """An arbitrary named collection of members -- deliberately generic, not
    team-shaped and not aware of any specific use: "all coaches", "all team
    managers", an ad-hoc committee. A Team's roster is a separate, more
    specific concept (teams.TeamMembership); nothing here assumes team
    semantics. Referee eligibility (who can ref which team) is a member-level
    fact (teams.RefereeProfile), not a Group concern -- Group carries no
    referee-specific knowledge at all."""

    name = models.CharField(_("name"), max_length=255, unique=True)

    class Meta:
        verbose_name = _("group")
        verbose_name_plural = _("groups")
        ordering = ["name"]

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
