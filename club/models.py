from django.contrib.auth.base_user import AbstractBaseUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from authentication.models import Member
from clubmanager.base import ClubScopedModel, UUIDModel


class Club(UUIDModel):
    name = models.CharField(_("name"), max_length=255)

    class Meta:
        verbose_name = _("club")
        verbose_name_plural = _("clubs")
        ordering = ["name"]

    def __str__(self):
        return self.name


class ClubMembership(UUIDModel):
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="member_of", verbose_name=_("member"))
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name="members", verbose_name=_("club"))

    license = models.CharField(_("license"), max_length=250, blank=True)

    class Meta:
        verbose_name = _("club membership")
        verbose_name_plural = _("club memberships")
        ordering = ["club", "member__last_name", "member__first_name"]
        unique_together = ("club", "member")

    def __str__(self):
        return f"{self.club} - {self.member}"
