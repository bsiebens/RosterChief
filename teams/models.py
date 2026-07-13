from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from club.models import Season
from clubmanager.base import ClubScopedModel, UUIDModel, validate_club_scope
from members.models import Member


class Team(ClubScopedModel):
    name = models.CharField(_("name"), max_length=255)
    short_name = models.CharField(_("short name"), max_length=255)

    class Meta:
        verbose_name = _("team")
        verbose_name_plural = _("teams")
        constraints = [
            models.UniqueConstraint(fields=["club", "name"], name="unique_team_name_per_club"),
        ]

    def __str__(self):
        return self.name


class Position(ClubScopedModel):
    name = models.CharField(_("name"), max_length=255)
    short_name = models.CharField(_("short name"), max_length=255)
    ordering = models.PositiveSmallIntegerField(_("ordering"), default=0)

    staff_position = models.BooleanField(_("staff position"), default=False)
    management_position = models.BooleanField(_("management position"), default=False)

    class Meta:
        verbose_name = _("position")
        verbose_name_plural = _("positions")
        constraints = [
            models.UniqueConstraint(fields=["club", "name"], name="unique_position_name_per_club"),
            # A management position is always a staff position.
            models.CheckConstraint(condition=Q(management_position=False) | Q(staff_position=True), name="management_position_implies_staff_position"),
        ]
        ordering = ["ordering", "name"]

    def __str__(self):
        return self.name


class TeamMembership(UUIDModel):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="roster", verbose_name=_("team"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="team_memberships", verbose_name=_("member"))
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="team_memberships", verbose_name=_("season"))
    position = models.ForeignKey(Position, on_delete=models.PROTECT, related_name="team_memberships", verbose_name=_("position"), limit_choices_to={"staff_position": False})

    jersey_number = models.PositiveSmallIntegerField(_("jersey number"), blank=True, null=True)
    is_captain = models.BooleanField(_("is captain"), default=False)
    is_alternate_captain = models.BooleanField(_("is alternate captain"), default=False)

    class Meta:
        verbose_name = _("team membership")
        verbose_name_plural = _("team memberships")
        ordering = ["team", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["team", "season", "member"], name="unique_member_per_team_per_season"),
            models.UniqueConstraint(fields=["team", "season", "jersey_number"], name="unique_jersey_number_per_team_per_season"),
        ]

    def __str__(self):
        return f"{self.team} - {self.member}"

    def clean(self):
        club_id = self.team.club_id if self.team_id else None
        validate_club_scope(self, club_id, same_club_fields=("season", "position"))


class StaffAssignment(UUIDModel):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="staff_assignments", verbose_name=_("team"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="staff_assignments", verbose_name=_("member"))
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="staff_assignments", verbose_name=_("season"))
    position = models.ForeignKey(Position, on_delete=models.PROTECT, related_name="staff_assignments", verbose_name=_("position"), limit_choices_to={"staff_position": True})

    class Meta:
        verbose_name = _("staff assignment")
        verbose_name_plural = _("staff assignments")
        ordering = ["team", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["team", "season", "member"], name="unique_staff_member_per_team_per_season"),
        ]

    def __str__(self):
        return f"{self.team} - {self.member}"

    def clean(self):
        club_id = self.team.club_id if self.team_id else None
        validate_club_scope(self, club_id, same_club_fields=("season", "position"))
