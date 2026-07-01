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
    """A person in the club. May or may not have a login (:attr:`user`)."""

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
    guardians = models.ManyToManyField(
        "self",
        through="Guardianship",
        through_fields=("child", "guardian"),
        symmetrical=False,
        related_name="dependents",
    )

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Guardianship(models.Model):
    """Directional guardian -> child relationship between two members."""

    class Relationship(models.TextChoices):
        PARENT = "parent", "Parent"
        GUARDIAN = "guardian", "Guardian"
        OTHER = "other", "Other"

    guardian = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="guardian_links",
    )
    child = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="child_links",
    )
    relationship = models.CharField(
        max_length=20,
        choices=Relationship.choices,
        default=Relationship.PARENT,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["guardian", "child"],
                name="unique_guardianship",
            ),
            models.CheckConstraint(
                condition=~models.Q(guardian=models.F("child")),
                name="guardian_not_self",
            ),
        ]

    def __str__(self):
        return f"{self.guardian} → {self.child} ({self.get_relationship_display()})"
