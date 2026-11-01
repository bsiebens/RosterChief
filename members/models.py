from django.conf import settings
from django.db import models

from rosterchief.base import UUIDModel


class Member(UUIDModel):
    """A person on a club's roster. Lives in the club schema. A login (``user``) is optional."""

    first_name = models.CharField(max_length=75)
    last_name = models.CharField(max_length=75)
    email = models.EmailField(blank=True, help_text="Contact address. Also used as the login email when access is granted.")
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()
