from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from phonenumber_field.modelfields import PhoneNumberField

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    """Login identity. Only people who actually sign in get a User.

    Personal/roster data lives on :class:`Member`; this model carries just the
    authentication identity. ``PermissionsMixin`` provides ``is_superuser``,
    ``groups`` and ``user_permissions`` (the basis for the access tiers).
    """

    email = models.EmailField(unique=True)
    is_staff = models.BooleanField(
        default=False,
        help_text="Whether the user can log into the admin site.",
    )
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.get_full_name()

    def get_full_name(self):
        member = getattr(self, "member", None)
        if member is not None:
            return f"{member.first_name} {member.last_name}".strip()
        return self.email

    def get_short_name(self):
        member = getattr(self, "member", None)
        if member is not None:
            return member.first_name
        return self.email


class Family(models.Model):
    """A household grouping members together."""

    name = models.CharField(max_length=150, help_text='e.g. "The Smiths"')
    address = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "families"

    def __str__(self):
        return self.name


class Member(models.Model):
    """A person in the club. May or may not have a login (:attr:`user`).

    Family relationships are modelled by membership in a :class:`Family`:
    guardians (``is_guardian=True``) look after the other members of the same
    family (the dependents).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="member",
    )

    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    # Contact email — optional. Children may have none; a login email lives on User.
    # Use `contact_email` to read it with a fallback to the linked user's login email.
    email = models.EmailField(blank=True)
    phone = PhoneNumberField(blank=True)
    emergency_phone = PhoneNumberField(blank=True)
    license_number = models.CharField(max_length=50, blank=True)
    date_of_birth = models.DateField()

    family = models.ForeignKey(
        Family,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="members",
    )
    is_guardian = models.BooleanField(
        default=False,
        help_text="Whether this member is a parent/guardian in their family.",
    )

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def contact_email(self):
        """Best email to reach this member: own contact email, else login email."""
        return self.email or (self.user.email if self.user_id else "")

    @property
    def guardians(self):
        """Members of my family who look after me (only if I'm a dependent)."""
        if self.is_guardian or self.family_id is None:
            return Member.objects.none()
        return self.family.members.filter(is_guardian=True)

    @property
    def dependents(self):
        """Members of my family I look after (only if I'm a guardian)."""
        if not self.is_guardian or self.family_id is None:
            return Member.objects.none()
        return self.family.members.filter(is_guardian=False)
