from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from club.models import Season
from members.models import Member
from rosterchief.base import ClubScopedModel, UUIDModel, validate_club_scope


class NumberPool(ClubScopedModel):
    """A club-defined jersey-number pool, e.g. "Youth", "Senior",
    "Goalkeepers". Several teams can share one pool (Team.pool below) --
    within a pool a number belongs to at most one holder at a time, checked
    across every team in the pool, not just one. See teams/services/numbers.py
    for the availability rules themselves; this model only owns the pool's
    identity and its valid number range."""

    name = models.CharField(_("name"), max_length=255)
    min_number = models.PositiveSmallIntegerField(_("minimum number"))
    max_number = models.PositiveSmallIntegerField(_("maximum number"))

    class Meta:
        verbose_name = _("number pool")
        verbose_name_plural = _("number pools")
        constraints = [
            models.UniqueConstraint(fields=["club", "name"], name="unique_number_pool_name_per_club"),
        ]
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.min_number is not None and self.max_number is not None and self.min_number > self.max_number:
            raise ValidationError({"max_number": _("Must be greater than or equal to the minimum number.")})


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
    pool = models.ForeignKey(
        NumberPool,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teams",
        verbose_name=_("number pool"),
        help_text=_("Jersey numbers are checked for clashes against every other team sharing this pool, not just this one. Left blank, this team's numbers are only checked against itself, as before."),
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

    def clean(self):
        club_id = self.club_id
        if club_id is not None:
            validate_club_scope(self, club_id, same_club_fields=("pool",))


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
    -- admin-managed, like Position, so a club can name its own levels rather
    than picking from a fixed list. Owns which teams it qualifies a referee
    for: eligibility is a property of the *level*, not of the individual
    referee -- a club typically has a handful of levels, each unlocking a
    tier of teams/competitions, rather than hand-picking teams per referee.

    `inherits_from` chains levels together so a higher tier doesn't need every
    lower tier's team re-added by hand: a "National" referee is automatically
    eligible for everything "Regional" (its inherits_from) covers, and so on
    down the chain -- see eligible_team_ids, the single definition every
    consumer (RefereeProfile.eligible_teams, events.services.referees) reads
    through. That chain is also what expresses which tier is "higher" now --
    there used to be a manual `ordering` field for this too, dropped once
    inherits_from covered the same need without a second, independently-kept
    number to keep in sync with it."""

    name = models.CharField(_("name"), max_length=255)
    teams = models.ManyToManyField(Team, related_name="referee_levels", blank=True, verbose_name=_("qualifies for"), help_text=_("Members holding this level can be assigned to referee these teams' home games."))
    inherits_from = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="inherited_by",
        verbose_name=_("inherits from"),
        help_text=_("A referee holding this level is also eligible for everything the linked level covers (and, transitively, whatever that one inherits from)."),
    )

    class Meta:
        verbose_name = _("referee level")
        verbose_name_plural = _("referee levels")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["club", "name"], name="unique_referee_level_name_per_club"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        # club_id is still None here for a brand-new level: RefereeLevelCreateView
        # (like its siblings -- PositionCreateView, GroupCreateView, ...) assigns
        # form.instance.club in form_valid(), *after* the form's is_valid() already
        # ran clean() -- so there's nothing to compare against yet on create. The
        # form's own inherits_from queryset (scoped to `club`) is what actually
        # blocks a cross-club pick there; this still catches it on every other path
        # (update, admin, direct .full_clean()) where club_id is already set.
        if self.club_id is not None:
            validate_club_scope(self, self.club_id, same_club_fields=("inherits_from",))
        current = self.inherits_from
        seen = set()
        while current is not None:
            if current.pk == self.pk:
                raise ValidationError({"inherits_from": _("This would create a loop -- a level can't inherit from itself, even indirectly.")})
            if current.pk in seen:
                break  # An already-broken chain elsewhere; not this field's problem to fix.
            seen.add(current.pk)
            current = current.inherits_from

    def eligible_team_ids(self):
        """This level's own qualifying teams, plus (transitively) every level
        it inherits from -- so a higher tier doesn't need a lower tier's teams
        duplicated onto it by hand, and stays correct if the lower tier's own
        teams change later. Cycle-guarded even though clean() already blocks
        creating one, in case of data written outside that path."""
        team_ids = set(self.teams.values_list("id", flat=True))
        seen = {self.pk}
        current = self.inherits_from
        while current is not None and current.pk not in seen:
            team_ids.update(current.teams.values_list("id", flat=True))
            seen.add(current.pk)
            current = current.inherits_from
        return team_ids


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
        isn't currently eligible, regardless of what level is set. Includes
        whatever the level inherits from, transitively -- see
        RefereeLevel.eligible_team_ids."""
        if not self.is_eligible:
            return Team.objects.none()
        return Team.objects.filter(id__in=self.level.eligible_team_ids())


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


class NumberReservation(ClubScopedModel):
    """A manual block on a number within a pool, not tied to any member --
    e.g. a retired shirt. Permanent until released by staff: unlike organic
    usage (see teams/services/numbers.py's season window), a reservation
    doesn't expire on its own after a season or two."""

    pool = models.ForeignKey(NumberPool, on_delete=models.CASCADE, related_name="reservations", verbose_name=_("pool"))
    number = models.PositiveSmallIntegerField(_("number"))
    note = models.CharField(_("note"), max_length=255, blank=True, help_text=_("e.g. \"Retired -- Jane Doe, #23\"."))
    reserved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="number_reservations", verbose_name=_("reserved by"))

    class Meta:
        verbose_name = _("number reservation")
        verbose_name_plural = _("number reservations")
        ordering = ["pool", "number"]
        constraints = [
            models.UniqueConstraint(fields=["pool", "number"], name="unique_number_per_pool_reservation"),
        ]

    def __str__(self):
        return f"{self.pool} #{self.number}"

    def clean(self):
        if self.club_id is not None:
            validate_club_scope(self, self.club_id, same_club_fields=("pool",))
        if self.pool_id and self.number is not None and not (self.pool.min_number <= self.number <= self.pool.max_number):
            raise ValidationError({"number": _("Must be within the pool's number range.")})
