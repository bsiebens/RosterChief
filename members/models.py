from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from phonenumber_field.modelfields import PhoneNumberField

from rosterchief.base import ClubScopedModel, UUIDModel


class Family(UUIDModel):
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
    def contact_email(self):
        """Best email to reach this member: own contact email, else login email."""
        return self.email or (self.user.email if self.user_id else "")

    @property
    def guardians(self):
        return Member.objects.filter(
            family_memberships__role__in=[FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN],
            family_memberships__family__memberships__member=self,
            family_memberships__family__memberships__role=FamilyMembership.FamilyRole.CHILD,
        ).distinct()


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


class ParentClaim(ClubScopedModel):
    """A parent asking to be linked to a child the club already has on file.

    The migration path this exists for: a club arrives with a list of children
    from a federation export and no parent records at all. The children are
    imported without logins (each into a family of their own, see
    members.services.claims.children_awaiting_a_parent), and parents come forward
    afterwards.

    Verification is a human decision, deliberately. The alternatives -- a claim
    code, or matching on name and date of birth -- either need a delivery channel
    the club may not have, or hand out someone else's child to whoever guesses a
    birthday. The club already knows its own families, so an admin approving from
    a queue is the only check that is actually worth anything.

    The parent's details are held here as plain text rather than as a User: this
    form is public, so creating an account per submission would let anyone fill
    the table with them. The account is created on approval, by which point a
    human has vouched for it.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("pending")
        APPROVED = "approved", _("approved")
        REJECTED = "rejected", _("rejected")

    parent_first_name = models.CharField(_("parent first name"), max_length=150)
    parent_last_name = models.CharField(_("parent last name"), max_length=150)
    parent_email = models.EmailField(_("parent email"))

    # What the parent typed, kept verbatim even after the claim is matched -- it is
    # the evidence the admin judged, and a later dispute needs to see it unchanged.
    child_first_name = models.CharField(_("child first name"), max_length=150)
    child_last_name = models.CharField(_("child last name"), max_length=150)
    child_date_of_birth = models.DateField(_("child date of birth"))

    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.PENDING)
    child = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="parent_claims", verbose_name=_("matched child"), help_text=_("Set when an admin approves the claim -- the child it was matched to."))
    reviewed_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("reviewed by"))
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)
    note = models.TextField(_("note"), blank=True, help_text=_("Why it was rejected, or anything worth recording about the decision."))

    class Meta:
        verbose_name = _("parent claim")
        verbose_name_plural = _("parent claims")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.parent_first_name} {self.parent_last_name} -> {self.child_first_name} {self.child_last_name}"

    @property
    def is_pending(self) -> bool:
        return self.status == self.Status.PENDING

    @property
    def claimed_child_name(self) -> str:
        return f"{self.child_first_name} {self.child_last_name}".strip()

    @property
    def parent_name(self) -> str:
        return f"{self.parent_first_name} {self.parent_last_name}".strip()


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
