from django.contrib.auth.base_user import BaseUserManager
from django.db.models import Q


class UserManager(BaseUserManager):
    """Manager for the email-based custom User model."""

    use_in_migrations = True

    def platform_admins(self):
        """Every account with platform-wide access (staff or superuser) --
        the one query shared by the control panel's own admin list
        (controlpanel.services.platform_admins.platform_admins), and by
        billing/bugs, which each need "who to email about this" without
        importing controlpanel the wrong way round (controlpanel depends on
        domain apps, not the other way)."""
        return self.filter(Q(is_staff=True) | Q(is_superuser=True))

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)
