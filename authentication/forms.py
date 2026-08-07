from django.contrib.auth.forms import BaseUserCreationForm
from django.contrib.auth.forms import UserChangeForm as DjangoUserChangeForm

from .models import User


class UserCreationForm(BaseUserCreationForm):
    """Add-user form for the email-based custom User (no ``username`` field)."""

    class Meta:
        model = User
        fields = ("email",)


class UserChangeForm(DjangoUserChangeForm):
    """Change-user form; keeps the read-only password hash widget."""

    class Meta:
        model = User
        fields = "__all__"
