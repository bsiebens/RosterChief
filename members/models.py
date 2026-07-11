from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from phonenumber_field.modelfields import PhoneNumberField

from clubmanager.base import UUIDModel


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
            return _("%(surnames)s family") % {"surnames": " / ".join(surnames)}
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
        unique_together = ("family", "member")

    def __str__(self):
        return f"{self.family} - {self.member} ({self.get_role_display()})"
