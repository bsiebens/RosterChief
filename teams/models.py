from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from club.models import Season
from members.models import Member
from rosterchief.base import ClubScopedModel, UUIDModel, validate_club_scope


class Team(ClubScopedModel):
    class RefereeManagement(models.TextChoices):
        CLUB = "club", _("Club")
        FEDERATION = "federation", _("Federation")

    name = models.CharField(_("name"), max_length=255)
    short_name = models.CharField(_("short name"), max_length=255)
    referee_management = models.CharField(
        _("referee management"),
        max_length=20,
        choices=RefereeManagement.choices,
        default=RefereeManagement.CLUB,
        help_text=_("Who arranges referees for this team's home games. Federation-managed teams are left out of the referee tools entirely -- no eligibility, no assignment, nothing to configure."),
    )

    class Meta:
        verbose_name = _("team")
        verbose_name_plural = _("teams")
        constraints = [
            models.UniqueConstraint(fields=["club", "name"], name="unique_team_name_per_club"),
        ]
        ordering = ["name"]

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
    position = models.ForeignKey(
        Position,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="team_memberships",
        verbose_name=_("position"),
        limit_choices_to={"staff_position": False},
        help_text=_("Left blank for a member placed on the roster before their position is decided (e.g. from the Sign-up page) -- the team's own manager sets it once known."),
    )

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


class RefereeLevel(ClubScopedModel):
    """A club-defined referee qualification tier (e.g. "Regional", "National")
    -- admin-managed, like Position, so a club can name and reorder its own
    levels rather than picking from a fixed list. Owns which teams it
    qualifies a referee for: eligibility is a property of the *level*, not of
    the individual referee -- a club typically has a handful of levels, each
    unlocking a tier of teams/competitions, rather than hand-picking teams per
    referee."""

    name = models.CharField(_("name"), max_length=255)
    ordering = models.PositiveSmallIntegerField(_("ordering"), default=0, help_text=_("Lower numbers are listed first. Levels with the same number are ordered by name."))
    teams = models.ManyToManyField(Team, related_name="referee_levels", blank=True, verbose_name=_("qualifies for"), help_text=_("Members holding this level can be assigned to referee these teams' home games."))

    class Meta:
        verbose_name = _("referee level")
        verbose_name_plural = _("referee levels")
        ordering = ["ordering", "name"]
        constraints = [
            models.UniqueConstraint(fields=["club", "name"], name="unique_referee_level_name_per_club"),
        ]

    def __str__(self):
        return self.name


class RefereeProfile(UUIDModel):
    """Marks a member as a club referee: their level (which determines which
    teams they're eligible for, via RefereeLevel.teams) and how long that
    qualification is valid. A member-level fact, not a group-level one --
    managed from the member's own page, unrelated to members.Group (which
    stays a plain, opaque collection of people with no referee-specific
    knowledge).

    No level, or an expired/unset validity, both mean "not currently
    eligible" -- see `is_currently_valid`/`eligible_teams` below, the single
    definitions every consumer (the event assign panel, the team page, the
    referees list) reads through, so "eligible" never drifts out of sync.
    """

    member = models.OneToOneField(Member, on_delete=models.CASCADE, related_name="referee_profile", verbose_name=_("member"))
    level = models.ForeignKey(RefereeLevel, on_delete=models.PROTECT, null=True, blank=True, related_name="referees", verbose_name=_("level"))
    valid_until = models.DateField(_("valid until"), null=True, blank=True, help_text=_("Once this date has passed, the referee is not eligible for assignment until it's extended."))

    class Meta:
        verbose_name = _("referee profile")
        verbose_name_plural = _("referee profiles")
        ordering = ["member__last_name", "member__first_name"]

    def __str__(self):
        return f"{self.member} (referee)"

    @property
    def is_currently_valid(self) -> bool:
        """Whether the validity date itself hasn't passed -- independent of
        whether a level is even set. Use `is_eligible` for the full gate."""
        return self.valid_until is not None and self.valid_until >= timezone.localdate()

    @property
    def is_eligible(self) -> bool:
        """The full gate consumed everywhere eligibility actually matters: a
        level is set, and its validity hasn't passed."""
        return self.level_id is not None and self.is_currently_valid

    @property
    def eligible_teams(self):
        """Teams this profile currently qualifies for -- empty whenever it
        isn't currently eligible, regardless of what level is set."""
        if not self.is_eligible:
            return Team.objects.none()
        return self.level.teams.all()


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
