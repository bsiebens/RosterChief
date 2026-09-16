from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from phonenumber_field.modelfields import PhoneNumberField

from rosterchief.base import ClubScopedModel, UUIDModel


def member_photo_path(instance, filename):
    # Keyed off the member's own id, not e.g. a club slug -- Member is the one
    # global model in this codebase (shared across every club a person
    # belongs to), so there's no single club to scope the path by. Same
    # "stable id, not a name/slug" reasoning as news_photo_path/team_photo_path.
    return f"members/{instance.pk}/{filename}"


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

    photo = models.ImageField(_("photo"), upload_to=member_photo_path, blank=True)
    #: Gates only the *public* surfaces (a club's public website/API roster) --
    #: someone already inside the club (coach roster, staff lists, the app's
    #: own "People I manage" rows) sees the photo regardless, same as they
    #: already see this member's name. Off by default: a photo uploaded for
    #: in-app use (e.g. a parent picking their own kid out of a roster list)
    #: shouldn't become world-visible without a separate, explicit opt-in --
    #: this matters most for underage players.
    photo_public_consent = models.BooleanField(_("show this photo on the public website/API"), default=False)

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
    def public_photo(self):
        """``self.photo``, but only once ``photo_public_consent`` says it can
        leave the club's own app/staff views -- the one gate every public
        surface (teams.api's roster endpoints, a club's public website) must
        go through, so consent can't be forgotten at a new call site."""
        return self.photo if self.photo and self.photo_public_consent else None

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
