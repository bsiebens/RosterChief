from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from club.models import Season
from members.models import Member
from rosterchief.base import ClubScopedModel, UUIDModel, validate_club_scope


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


def team_photo_path(instance, filename):
    return f"clubs/{instance.team.club.slug}/teams/{instance.team_id}/{instance.season.name}/{filename}"


class TeamPhoto(UUIDModel):
    """One team photo per season -- a team's makeup changes every season, so
    this can't be a plain field on Team. No club FK of its own: club is
    already reachable via team.club, same reasoning as NewsPhoto being owned
    by News rather than club-scoped itself."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="photos", verbose_name=_("team"))
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="team_photos", verbose_name=_("season"))
    image = models.ImageField(_("image"), upload_to=team_photo_path)

    class Meta:
        verbose_name = _("team photo")
        verbose_name_plural = _("team photos")
        constraints = [
            models.UniqueConstraint(fields=["team", "season"], name="unique_team_photo_per_season"),
        ]

    def __str__(self):
        return f"{self.team} - {self.season}"


class Position(ClubScopedModel):
    name = models.CharField(_("name"), max_length=255)
    short_name = models.CharField(_("short name"), max_length=255)
    ordering = models.PositiveSmallIntegerField(_("ordering"), default=0, help_text=_("Lower numbers are listed first (e.g. on a team's roster). Positions with the same number are ordered by name."))

    staff_position = models.BooleanField(_("staff position"), default=False, help_text=_("Coach, manager, physio, ... -- assignable via a StaffAssignment rather than a team roster spot."))
    management_position = models.BooleanField(_("management position"), default=False, help_text=_("A staff position with management authority over the team (e.g. head coach) -- requires staff position to also be checked."))

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
