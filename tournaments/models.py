"""Home-tournament hosting: flexible-format brackets, multi-field scheduling,
guest-team registration, and volunteer shift sign-up -- one per-club,
feature-flagged app. See the design spec (branch: tournaments) for the full
rationale; each model below cites the existing convention it mirrors.

Gated by the "tournaments" waffle flag (features.models.Flag, per-club like
"shop"/"evaluations"/"officials") -- seeded disabled everywhere by this app's
0002 migration, same as evaluations/migrations/0002_create_evaluations_flag.py.
"""

import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from rosterchief.base import ClubScopedModel, UUIDModel, unique_slugify, validate_club_scope


def _generate_status_token() -> str:
    return secrets.token_urlsafe(32)


class Tournament(ClubScopedModel):
    """The aggregate root -- one home tournament. ``event`` is an optional
    link to a real calendar Event (kind=TOURNAMENT, which already exists and
    already treats a home tournament like a home game for referee/official
    eligibility) so the tournament can show on the club calendar and inherit
    that tooling for free; individual Match rows below deliberately stay
    lightweight, not one Event each."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        PUBLISHED = "published", _("Published")
        IN_PROGRESS = "in_progress", _("In progress")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")

    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=255, blank=True)
    slug_source = "name"

    season = models.ForeignKey("club.Season", on_delete=models.PROTECT, related_name="tournaments", verbose_name=_("season"))
    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"))
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        help_text=_("Draft: hidden from the public list, link still works. Published: listed publicly. In progress / Completed: informational once the tournament is underway or done. Cancelled: hidden and closed to entries."),
    )
    #: A manual override, independent of ``status`` -- publishing a
    #: tournament (making its page and registration form public) and
    #: registration actually still being open are two different questions:
    #: a club may want to close entries early (full, deadline passed)
    #: without touching ``status`` at all. ``accepts_registrations`` below
    #: is what everything else should actually check.
    registration_open = models.BooleanField(_("registration open"), default=True, help_text=_("Turn off to stop accepting new entries even while the tournament is still published -- e.g. once it's full."))
    #: A soft cap, checked live against non-withdrawn TournamentTeam rows
    #: (see ``accepts_registrations`` below) rather than by flipping
    #: ``registration_open`` itself -- so a withdrawal automatically
    #: reopens a slot without staff having to remember to toggle anything
    #: back on. Blank means uncapped.
    max_teams = models.PositiveSmallIntegerField(_("maximum teams"), null=True, blank=True, help_text=_("Registration closes automatically once this many teams are approved. Leave blank for no cap."))
    #: A soft, read-time-only flag -- never enforced anywhere (a registering
    #: team can still submit a too-old/too-young player, "no warning" is the
    #: whole point), just something staff can spot and check on the Teams
    #: list / roster screen. Age is computed as of ``start_date``, not
    #: today's date, since that's the date that actually matters for
    #: eligibility.
    min_age = models.PositiveSmallIntegerField(_("minimum age"), null=True, blank=True, help_text=_("Age as of the tournament's start date. Leave blank for no minimum -- players outside the range are flagged for staff to check, never blocked."))
    max_age = models.PositiveSmallIntegerField(_("maximum age"), null=True, blank=True, help_text=_("Age as of the tournament's start date. Leave blank for no maximum."))

    class LunchMode(models.TextChoices):
        NONE = "none", _("No lunch")
        OPTIONAL = "optional", _("Optional")
        MANDATORY = "mandatory", _("Mandatory")

    #: A partial amount due to secure a spot, distinct from the full
    #: ``entry_fee_amount`` -- both are informational for staff to track
    #: against ``TournamentTeam.payment_status`` by hand (no invoice/payment
    #: machinery here, unlike club.models.DuesInvoice's own fee flow; a home
    #: tournament's entry fee is typically settled by bank transfer or cash
    #: on the day, not through this platform).
    deposit_amount = models.DecimalField(
        _("deposit amount"), max_digits=8, decimal_places=2, null=True, blank=True, help_text=_("A partial amount due to secure a spot, ahead of the full entry fee. Leave blank if this tournament doesn't collect a separate deposit.")
    )
    entry_fee_amount = models.DecimalField(_("entry fee"), max_digits=8, decimal_places=2, null=True, blank=True, help_text=_("The full amount a team owes to take part. Leave blank if entry is free."))
    lunch_mode = models.CharField(
        _("lunch"), max_length=10, choices=LunchMode.choices, default=LunchMode.NONE, help_text=_("Optional: each player/staff member picks which tournament days they need lunch on. Mandatory: lunch is included for everyone, every day.")
    )
    lunch_price_per_day = models.DecimalField(_("lunch price per day"), max_digits=6, decimal_places=2, null=True, blank=True, help_text=_("Per person, per day -- shown alongside a team's lunch picks so staff can work out what's owed."))
    #: Shown on both the public tournament page and the registration page
    #: itself -- "who do I email about this tournament", distinct from a
    #: registering team's own ``foreign_contact_email`` (who the club should
    #: reach *them* at). One tournament-wide address, not per-team.
    contact_email = models.EmailField(_("contact email"), blank=True, help_text=_("Shown publicly on the tournament and registration pages, for questions about this tournament."))
    #: Markdown source, rendered through news.services.render_body_html
    #: wherever it's actually shown -- the same "one render function, called
    #: everywhere, never raw source" discipline that module's own docstring
    #: describes for News.
    description = models.TextField(
        _("description"),
        blank=True,
        help_text=_("Supports Markdown: **bold**, *italic*, [link text](https://example.com), # heading, - list item, 1. numbered item, ```code```. Rendered to HTML on the public tournament page."),
    )

    event = models.OneToOneField("events.Event", on_delete=models.SET_NULL, null=True, blank=True, related_name="tournament", verbose_name=_("calendar event"))
    #: The host club's own team playing in its own tournament -- set at
    #: creation (or added later from Settings), auto-added to Teams and
    #: never billed (tournaments.services.registration.ensure_home_team,
    #: called from TournamentCreateView/TournamentUpdateView.form_valid),
    #: with its own auto-created ``event`` so normal RSVP/signup decides its
    #: roster instead of a snapshot -- see TournamentRosterEntry's own
    #: "always live for a home team" signal (tournaments/signals.py).
    #: SET_NULL rather than PROTECT: the club deleting/reassigning this team
    #: shouldn't be blocked by a past tournament still pointing at it, and
    #: the already-created TournamentTeam/roster stand on their own either
    #: way (unaffected by home_team going blank afterwards).
    home_team = models.ForeignKey(
        "teams.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="home_tournaments",
        verbose_name=_("home team"),
        help_text=_("Your own team playing in this tournament. Automatically added to Teams (can't be withdrawn), marked fully paid, and given its own calendar event so normal sign-up decides its roster."),
    )

    class Meta:
        verbose_name = _("tournament")
        verbose_name_plural = _("tournaments")
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(fields=["club", "slug"], name="unique_tournament_slug_per_club"),
        ]

    def __str__(self):
        return self.name

    @property
    def accepts_registrations(self) -> bool:
        """Whether a new team can apply right now -- the status (only DRAFT/
        PUBLISHED even have a registration concept at all; once a tournament
        is under way, finished, or cancelled there's nothing left to
        register for), the manual ``registration_open`` toggle, and (when
        set) ``max_teams`` all have to agree. The one thing every public
        "Register" CTA and the registration view itself should check, so
        they can never disagree about whether entries are actually open.

        The capacity check is computed live against non-withdrawn
        ``TournamentTeam`` rows rather than persisted anywhere, so a later
        withdrawal (freeing a slot) or a raised ``max_teams`` is reflected
        immediately without anything else having to notice and react."""
        if self.status not in (self.Status.DRAFT, self.Status.PUBLISHED) or not self.registration_open:
            return False
        if self.max_teams is not None and self.teams.filter(withdrawn_at__isnull=True).count() >= self.max_teams:
            return False
        return True

    @property
    def location(self):
        """Where this tournament is actually played -- the linked Event's
        own location if one is set, otherwise the club's home ground. Not a
        field of its own: a tournament's venue is exactly whichever of those
        two already-real things applies, and duplicating it here would just
        be one more place to get out of sync with the Event or the club's
        own ground. Local import, not a top-level one -- every other
        cross-app reference in this file is a lazy string FK for the same
        reason (avoiding import-order coupling to the events app)."""
        from events.models import Location

        if self.event_id is not None and self.event.location_id is not None:
            return self.event.location
        return Location.objects.filter(club_id=self.club_id, is_home=True).first()

    def date_range(self) -> list:
        """Every calendar day this tournament runs, inclusive -- e.g. the
        roster's per-day lunch picker only ever offers days the tournament
        is actually on."""
        return [self.start_date + timedelta(days=offset) for offset in range((self.end_date - self.start_date).days + 1)]

    def age_out_of_range(self, date_of_birth) -> bool:
        """Whether a player born on ``date_of_birth`` falls outside
        ``[min_age, max_age]`` as of this tournament's own ``start_date`` --
        a single shared age-math home for the Teams list and roster screen
        (both need the same computation; neither should reimplement it).
        Always ``False`` when there's no birth date to check or neither
        bound is set."""
        if date_of_birth is None or (self.min_age is None and self.max_age is None):
            return False
        age = self.start_date.year - date_of_birth.year - ((self.start_date.month, self.start_date.day) < (date_of_birth.month, date_of_birth.day))
        if self.min_age is not None and age < self.min_age:
            return True
        return self.max_age is not None and age > self.max_age

    def clean(self):
        if self.event_id is not None:
            if self.event.club_id != self.club_id:
                raise ValidationError({"event": _("Must belong to the same club.")})
            if self.event.season_id is not None and self.event.season_id != self.season_id:
                raise ValidationError({"event": _("Must belong to the same season.")})


def tournament_field_availability_default():
    """Empty per-day availability list -- see TournamentField.availability."""
    return []


class TournamentField(UUIDModel):
    """One bookable playing surface for a tournament's own duration -- not the
    same thing as ``events.Location`` (one permanent venue record per club,
    no sub-surface or availability-window concept), so a 3-pitch tournament
    needs its own resource rather than three fake Locations. Not itself
    club-scoped -- its club is reached through ``tournament``, same
    reasoning as ``club.models.FeePayment``.

    No location of its own -- a field's venue is just wherever the
    tournament itself is played (``Tournament.location``); every field in a
    tournament sharing one venue is the common case, and a per-field
    override was one more place for staff to have to get right (and
    possibly disagree with the tournament's own location) for a shape that
    almost never comes up in practice.

    ``availability`` is a list of per-day windows rather than one start/end
    pair, so a multi-day tournament can open a field at different hours on
    different days: ``[{"date": "2026-09-19", "start": "08:00", "end": "20:00"}, ...]``.
    """

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="fields", verbose_name=_("tournament"))
    name = models.CharField(_("name"), max_length=255)
    availability = models.JSONField(_("availability"), default=tournament_field_availability_default, blank=True, help_text=_('Per-day windows, e.g. [{"date": "2026-09-19", "start": "08:00", "end": "20:00"}].'))

    class Meta:
        verbose_name = _("tournament field")
        verbose_name_plural = _("tournament fields")
        ordering = ["tournament", "name"]

    def __str__(self):
        return f"{self.tournament} - {self.name}"


class BreakRule(UUIDModel):
    """One rule in a tournament's break policy -- e.g. "5 minutes between any
    two games" (the mandatory default every tournament falls back to when no
    rule fires, enforced in ``tournaments.services.scheduling``, not stored
    as a row itself) plus "every 3rd game on Field 2 needs 15 minutes
    instead" or "no games 12:00-13:00, any field". Not itself club-scoped --
    its club is reached through ``tournament``.

    Rules are evaluated in ``priority`` order (lower first), first match
    wins, same "ordered list, first match wins, mandatory fallback" shape as
    a firewall ruleset -- there is no single query that expresses "the right
    gap for this candidate slot," so this is deliberately a rule *engine*
    the scheduler consults, not a field on ``TournamentField`` itself.

    ``field`` null means tournament-wide (applies to every field); set means
    that one field only. Exactly one of the trigger-specific field groups
    below is meaningful, selected by ``trigger_type`` -- enforced in
    ``clean()`` rather than a ``CheckConstraint`` (four-way, more than any
    existing xor pair in this codebase, same reasoning ``Match.clean()``
    gives for its own three-way per-side check).
    """

    class TriggerType(models.TextChoices):
        GAME_COUNT = "game_count", _("every N games on this field")
        ELAPSED_TIME = "elapsed_time", _("every N minutes of play on this field")
        ROUND_BOUNDARY = "round_boundary", _("after a specific round finishes on this field")
        CLOCK_WINDOW = "clock_window", _("blackout window (no games may overlap it)")

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="break_rules", verbose_name=_("tournament"))
    field = models.ForeignKey(TournamentField, on_delete=models.CASCADE, null=True, blank=True, related_name="break_rules", verbose_name=_("field"), help_text=_("Left blank to apply to every field."))
    trigger_type = models.CharField(_("trigger"), max_length=20, choices=TriggerType.choices)

    # GAME_COUNT
    trigger_every_n_games = models.PositiveSmallIntegerField(_("every N games"), null=True, blank=True)
    # ELAPSED_TIME
    trigger_every_n_minutes = models.PositiveSmallIntegerField(_("every N minutes of play"), null=True, blank=True)
    # ROUND_BOUNDARY
    trigger_after_round_number = models.PositiveSmallIntegerField(_("after round number"), null=True, blank=True)
    # CLOCK_WINDOW
    trigger_window_start = models.TimeField(_("blackout start"), null=True, blank=True)
    trigger_window_end = models.TimeField(_("blackout end"), null=True, blank=True)

    #: Minutes to insert before the next game. Meaningless (left null) for
    #: CLOCK_WINDOW, which is a hard exclusion band, not a gap to add.
    gap_minutes = models.PositiveSmallIntegerField(_("gap (minutes)"), null=True, blank=True)
    #: True: this gap stands in place of the tournament's own mandatory
    #: default gap. False: it's additive, inserted on top of the default.
    replaces_default_gap = models.BooleanField(_("replaces the default gap"), default=True)
    priority = models.PositiveSmallIntegerField(_("priority"), default=0, help_text=_("Lower runs first; the first matching rule wins."))

    class Meta:
        verbose_name = _("break rule")
        verbose_name_plural = _("break rules")
        ordering = ["tournament", "priority"]

    def __str__(self):
        return f"{self.tournament} - {self.get_trigger_type_display()}"

    def clean(self):
        errors = {}
        if self.trigger_type == self.TriggerType.GAME_COUNT:
            if not self.trigger_every_n_games:
                errors["trigger_every_n_games"] = _("Required for this trigger.")
        elif self.trigger_type == self.TriggerType.ELAPSED_TIME:
            if not self.trigger_every_n_minutes:
                errors["trigger_every_n_minutes"] = _("Required for this trigger.")
        elif self.trigger_type == self.TriggerType.ROUND_BOUNDARY:
            if not self.trigger_after_round_number:
                errors["trigger_after_round_number"] = _("Required for this trigger.")
        elif self.trigger_type == self.TriggerType.CLOCK_WINDOW:
            if not self.trigger_window_start or not self.trigger_window_end:
                errors["trigger_window_start"] = _("Both a start and an end are required for this trigger.")
            elif self.trigger_window_end <= self.trigger_window_start:
                errors["trigger_window_end"] = _("Must be after the start.")

        if self.trigger_type != self.TriggerType.CLOCK_WINDOW and not self.gap_minutes:
            errors["gap_minutes"] = _("Required for every trigger except a blackout window.")

        if errors:
            raise ValidationError(errors)


class TournamentStage(UUIDModel):
    """One phase of a tournament's format -- "Group Stage", then "Final Four".
    Not itself club-scoped -- its club is reached through ``tournament``.

    ``format`` plus ``config`` (a JSON blob for format-specific parameters:
    Swiss round count, group size, seeding method, best-of) is deliberately
    one shape for every format instead of a schema per format -- the same
    "enum + flexible JSON" idiom already used by
    ``club.models.OnboardingRequirement.blocked_event_kinds`` and
    ``events.models.EventSeries.excluded_dates``. ``standings_config`` (points
    per win/draw/loss, tie-break order) lives per-stage rather than
    per-tournament since a group stage and a Swiss stage in the same
    tournament plausibly need different rules.
    """

    class Format(models.TextChoices):
        ROUND_ROBIN = "round_robin", _("round robin")
        GROUPS = "groups", _("groups")
        SINGLE_ELIM = "single_elim", _("single elimination")
        DOUBLE_ELIM = "double_elim", _("double elimination")
        SWISS = "swiss", _("Swiss")
        CUSTOM = "custom", _("custom")

    class Status(models.TextChoices):
        PENDING = "pending", _("pending")
        ACTIVE = "active", _("active")
        COMPLETE = "complete", _("complete")

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="stages", verbose_name=_("tournament"))
    name = models.CharField(_("name"), max_length=255)
    sequence = models.PositiveSmallIntegerField(_("sequence"), default=0)
    format = models.CharField(_("format"), max_length=20, choices=Format.choices)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.PENDING)
    config = models.JSONField(_("configuration"), default=dict, blank=True, help_text=_("Format-specific parameters (round count, group size, seeding method, ...)."))
    standings_config = models.JSONField(_("standings configuration"), default=dict, blank=True, help_text=_('Points/tie-break rules for this stage, e.g. {"points": {"win": 3, "draw": 1, "loss": 0}, "tiebreakers": ["goal_diff", "goals_for"]}.'))

    class Meta:
        verbose_name = _("tournament stage")
        verbose_name_plural = _("tournament stages")
        ordering = ["tournament", "sequence"]

    def __str__(self):
        return f"{self.tournament} - {self.name}"


class TournamentGroup(UUIDModel):
    """Populated only for group-flavoured stages, including a groups stage
    that later feeds a knockout stage. Not itself club-scoped -- its club is
    reached through ``stage``."""

    stage = models.ForeignKey(TournamentStage, on_delete=models.CASCADE, related_name="groups", verbose_name=_("stage"))
    name = models.CharField(_("name"), max_length=255)
    sequence = models.PositiveSmallIntegerField(_("sequence"), default=0)

    class Meta:
        verbose_name = _("tournament group")
        verbose_name_plural = _("tournament groups")
        ordering = ["stage", "sequence"]

    def __str__(self):
        return f"{self.stage} - {self.name}"


class TournamentRegistration(UUIDModel):
    """A public, no-login application to enter a tournament -- mirrors
    ``registration.models.RegistrationDetails``'s request/decision split
    exactly. Every submission starts pending; only an approval creates the
    live ``TournamentTeam`` (``resulting_tournament_team``). Not itself
    club-scoped -- its club is reached through ``tournament``.

    Team identity is a ``team`` FK *xor* the ``foreign_*`` fields, the same
    "linked entity vs. free-standing data" idiom ``events.EventReferee``
    already uses for ``member``/``external_name``. The host club's own team
    can skip this entirely -- see ``TournamentTeam.registration`` (nullable).
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("pending")
        APPROVED = "approved", _("approved")
        REJECTED = "rejected", _("rejected")

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="registrations", verbose_name=_("tournament"))

    team = models.ForeignKey("teams.Team", on_delete=models.SET_NULL, null=True, blank=True, related_name="tournament_registrations", verbose_name=_("team"))
    foreign_name = models.CharField(_("team name"), max_length=255, blank=True)
    foreign_contact_name = models.CharField(_("contact name"), max_length=255, blank=True)
    foreign_contact_email = models.EmailField(_("contact email"), blank=True)
    foreign_contact_phone = models.CharField(_("contact phone"), max_length=32, blank=True)
    foreign_primary_color = models.CharField(_("primary color"), max_length=7, blank=True)
    foreign_secondary_color = models.CharField(_("secondary color"), max_length=7, blank=True)
    foreign_logo = models.ImageField(_("logo"), upload_to="tournaments/registrations/logos", blank=True)

    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.PENDING)
    rejection_reason = models.TextField(_("rejection reason"), blank=True)
    status_token = models.CharField(_("status token"), max_length=64, unique=True, default=_generate_status_token, editable=False)
    submitted_at = models.DateTimeField(_("submitted at"), auto_now_add=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("reviewed by"))
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("tournament registration")
        verbose_name_plural = _("tournament registrations")
        ordering = ["-submitted_at"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(team__isnull=False) & Q(foreign_name="")) | (Q(team__isnull=True) & ~Q(foreign_name="")),
                name="tournament_registration_team_xor_foreign_name",
            ),
        ]

    def __str__(self):
        return f"{self.tournament} - {self.display_name}"

    @property
    def display_name(self) -> str:
        return str(self.team) if self.team_id else self.foreign_name

    @property
    def is_foreign(self) -> bool:
        return self.team_id is None


class TournamentRosterRole(models.TextChoices):
    """Shared between ``TournamentRegistrationPlayer`` and
    ``TournamentRosterEntry`` -- one person on a tournament roster is either
    a player or a staff member (coach, physio, ...); a staff row has no
    ``date_of_birth`` (see both models' own ``clean()``) but otherwise shares
    every field a player row has (position, license number, dietary
    restrictions, lunch days)."""

    PLAYER = "player", _("Player")
    STAFF = "staff", _("Staff")


class TournamentRegistrationPlayer(UUIDModel):
    """One roster line submitted with a pending ``TournamentRegistration`` --
    the only place a foreign team's players can be captured before
    approval, since ``TournamentRosterEntry`` (the live roster) requires an
    already-approved ``TournamentTeam`` that doesn't exist yet at submission
    time. Copied into real ``TournamentRosterEntry`` rows by
    ``tournaments.services.registration.approve_registration`` -- kept as a
    separate model rather than letting ``TournamentRosterEntry.tournament_team``
    go nullable, so "submitted, not yet real" and "on the live roster" stay
    two distinct, un-mixable states the same way ``TournamentRegistration``
    and ``TournamentTeam`` themselves do. Meaningless for a club-team
    registration (that roster comes from ``TeamMembership`` at approval
    time instead) -- not enforced by a constraint, since staff may still
    submit a team-linked registration with a proposed line-up here that gets
    replaced by the real roster on approval, and that's a legitimate,
    non-error use. Not itself club-scoped -- its club is reached through
    ``registration``.

    Carries the same extra fields ``TournamentRosterEntry`` does (see that
    model's own docstring for why they're duplicated rather than shared
    through an abstract base -- the two models' name fields don't share a
    shape, a flat first/last name here vs. member-or-foreign-name there) so
    nothing is lost when a foreign team's submitted roster is copied onto
    the live one at approval.
    """

    registration = models.ForeignKey(TournamentRegistration, on_delete=models.CASCADE, related_name="players", verbose_name=_("registration"))
    first_name = models.CharField(_("first name"), max_length=150)
    last_name = models.CharField(_("last name"), max_length=150)
    jersey_number = models.PositiveSmallIntegerField(_("jersey number"), null=True, blank=True)
    role = models.CharField(_("role"), max_length=10, choices=TournamentRosterRole.choices, default=TournamentRosterRole.PLAYER)
    position_label = models.CharField(_("position"), max_length=100, blank=True)
    date_of_birth = models.DateField(_("date of birth"), null=True, blank=True, help_text=_("Players only."))
    license_number = models.CharField(_("license number"), max_length=100, blank=True)
    dietary_restrictions = models.TextField(_("dietary restrictions"), blank=True, help_text=_("Allergies, intolerances, or anything the kitchen should know about."))
    #: List of ISO date strings, a subset of the tournament's own
    #: ``date_range()`` -- meaningless while ``Tournament.lunch_mode`` is
    #: NONE, and (by convention, not a constraint -- see the row form's own
    #: handling) every tournament day when MANDATORY rather than left empty,
    #: so a plain ``len()`` here always gives the right day count either way.
    lunch_days = models.JSONField(_("lunch days"), default=list, blank=True)

    class Meta:
        verbose_name = _("tournament registration player")
        verbose_name_plural = _("tournament registration players")
        ordering = ["registration", "jersey_number"]

    def __str__(self):
        return f"{self.registration} - {self.first_name} {self.last_name}".strip()

    def clean(self):
        if self.role == TournamentRosterRole.STAFF and self.date_of_birth is not None:
            raise ValidationError({"date_of_birth": _("Staff rows don't need a date of birth.")})


class TournamentTeam(ClubScopedModel):
    """The entity that actually appears on the bracket -- created on
    registration approval (``registration`` set), or directly by tournament
    staff for the host club's own team (``registration`` left null, prefilled
    from the real Team/roster, skipping the public queue by design).

    Team identity is the same ``team`` xor ``foreign_*`` pair as
    ``TournamentRegistration``. ``withdrawn_at`` is a soft state, matching
    ``club.models.Club.archived_at``, not a delete.
    """

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="teams", verbose_name=_("tournament"))
    registration = models.OneToOneField(TournamentRegistration, on_delete=models.SET_NULL, null=True, blank=True, related_name="tournament_team", verbose_name=_("registration"))
    #: A plain public link, not a secret token -- a team's own slate of
    #: matches is public information by design (see the design spec's
    #: public-site section), unlike TournamentRegistration.status_token
    #: which gates contact details. Scoped per-tournament, not per-club (see
    #: this model's own save() override) -- a name collision with a team in
    #: some other tournament shouldn't force an unnecessary "-2" suffix.
    slug = models.SlugField(_("slug"), max_length=255, blank=True)

    team = models.ForeignKey("teams.Team", on_delete=models.SET_NULL, null=True, blank=True, related_name="tournament_entries", verbose_name=_("team"))
    foreign_name = models.CharField(_("team name"), max_length=255, blank=True)
    foreign_contact_name = models.CharField(_("contact name"), max_length=255, blank=True)
    foreign_contact_email = models.EmailField(_("contact email"), blank=True)
    foreign_contact_phone = models.CharField(_("contact phone"), max_length=32, blank=True)
    foreign_primary_color = models.CharField(_("primary color"), max_length=7, blank=True)
    foreign_secondary_color = models.CharField(_("secondary color"), max_length=7, blank=True)
    foreign_logo = models.ImageField(_("logo"), upload_to="tournaments/teams/logos", blank=True)

    seed = models.PositiveSmallIntegerField(_("seed"), null=True, blank=True)
    withdrawn_at = models.DateTimeField(_("withdrawn at"), null=True, blank=True)
    #: Shown back to the team on its own status page (TournamentRegistrationStatusView)
    #: as well as to staff -- same "reason travels with the decision" idiom
    #: TournamentRegistration.rejection_reason already uses.
    withdrawal_reason = models.TextField(_("withdrawal reason"), blank=True)

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", _("Unpaid")
        DEPOSIT_RECEIVED = "deposit_received", _("Deposit received")
        FULLY_PAID = "fully_paid", _("Fully paid")

    #: Set by hand from the tournament's own Teams tab -- no invoice/payment
    #: machinery here (see Tournament.deposit_amount's own docstring for
    #: why), just a staff-tracked status against those two reference amounts.
    payment_status = models.CharField(_("payment status"), max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)

    class Meta:
        verbose_name = _("tournament team")
        verbose_name_plural = _("tournament teams")
        ordering = ["tournament", "seed"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(team__isnull=False) & Q(foreign_name="")) | (Q(team__isnull=True) & ~Q(foreign_name="")),
                name="tournament_team_team_xor_foreign_name",
            ),
            models.UniqueConstraint(fields=["tournament", "team"], condition=Q(team__isnull=False), name="unique_real_team_per_tournament"),
            models.UniqueConstraint(fields=["tournament", "slug"], name="unique_tournament_team_slug_per_tournament"),
        ]

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        # Deliberately not ClubScopedModel's own slug_source mechanism --
        # that always scopes uniqueness by club, and this needs per-tournament
        # scoping instead (see the slug field's own docstring).
        if not self.slug:
            self.slug = unique_slugify(self, self.display_name, scope={"tournament_id": self.tournament_id})
        super().save(*args, **kwargs)

    def clean(self):
        if self.team_id is not None:
            validate_club_scope(self, self.club_id, same_club_fields=("team",))

    @property
    def display_name(self) -> str:
        return str(self.team) if self.team_id else self.foreign_name

    @property
    def display_logo(self):
        """The logo to show for this team -- its own ``foreign_logo`` when
        set (a foreign team, or a club team someone uploaded one for
        anyway), otherwise the club's own crest for a club-linked team (a
        manually-added or home team has no logo of its own to upload --
        it's just the club playing under its own name). Read live off
        ``self.club`` rather than copied at creation time, so it stays
        current if the club's crest ever changes. ``None`` when neither
        applies (a foreign team with no logo uploaded)."""
        if self.foreign_logo:
            return self.foreign_logo
        if self.team_id:
            return self.club.logo or None
        return None


class TournamentRosterEntry(UUIDModel):
    """One player on a ``TournamentTeam``'s roster. For a club team, copied
    from ``teams.TeamMembership`` at roster-lock time rather than read live --
    snapshotted, not trailing its source, the same reasoning
    ``club.models.DuesInvoice.amount`` doesn't re-read from its membership
    afterwards -- so a mid-tournament roster edit on the parent team can't
    retroactively change tournament eligibility. A foreign team's players
    have no ``Member``/``User`` at all, just a name. Not itself club-scoped --
    its club is reached through ``tournament_team``.
    """

    tournament_team = models.ForeignKey(TournamentTeam, on_delete=models.CASCADE, related_name="roster_entries", verbose_name=_("tournament team"))
    member = models.ForeignKey("members.Member", on_delete=models.CASCADE, null=True, blank=True, related_name="tournament_roster_entries", verbose_name=_("member"))
    foreign_first_name = models.CharField(_("first name"), max_length=150, blank=True)
    foreign_last_name = models.CharField(_("last name"), max_length=150, blank=True)
    jersey_number = models.PositiveSmallIntegerField(_("jersey number"), null=True, blank=True)
    role = models.CharField(_("role"), max_length=10, choices=TournamentRosterRole.choices, default=TournamentRosterRole.PLAYER)
    position_label = models.CharField(_("position"), max_length=100, blank=True)
    date_of_birth = models.DateField(_("date of birth"), null=True, blank=True, help_text=_("Players only."))
    license_number = models.CharField(_("license number"), max_length=100, blank=True)
    dietary_restrictions = models.TextField(_("dietary restrictions"), blank=True, help_text=_("Allergies, intolerances, or anything the kitchen should know about."))
    #: See ``TournamentRegistrationPlayer.lunch_days`` -- same shape, same
    #: "every tournament day when MANDATORY, not left empty" convention.
    lunch_days = models.JSONField(_("lunch days"), default=list, blank=True)
    #: Lets staff on the roster screen see when a foreign team last touched
    #: its own roster through TournamentRegistrationStatusView's public
    #: edit path -- that write already lands here live, this just makes
    #: *when* visible rather than silent. Also ticks on the home team's own
    #: live-sync writes (tournaments/signals.py) and any staff edit here.
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("tournament roster entry")
        verbose_name_plural = _("tournament roster entries")
        ordering = ["tournament_team", "jersey_number"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(member__isnull=False) & Q(foreign_first_name="") & Q(foreign_last_name="")) | (Q(member__isnull=True) & ~Q(foreign_first_name="") & ~Q(foreign_last_name="")),
                name="tournament_roster_entry_member_xor_foreign_name",
            ),
            models.UniqueConstraint(fields=["tournament_team", "jersey_number"], condition=Q(jersey_number__isnull=False), name="unique_jersey_number_per_tournament_team"),
        ]

    def __str__(self):
        return self.display_name

    def clean(self):
        if self.role == TournamentRosterRole.STAFF and self.date_of_birth is not None:
            raise ValidationError({"date_of_birth": _("Staff rows don't need a date of birth.")})

    @property
    def display_name(self) -> str:
        return str(self.member) if self.member_id else f"{self.foreign_first_name} {self.foreign_last_name}".strip()


class Match(UUIDModel):
    """One game. Deliberately *not* an ``events.Event`` row -- one Event per
    match would drag in RSVP semantics that don't fit a bracket game and
    could mean dozens of rows per tournament; referee/official assignment
    happens at this level instead (``MatchReferee``/``MatchOfficial``) rather
    than only at the whole-tournament Event. Not itself club-scoped -- its
    club is reached through ``stage``.

    Each side resolves through exactly one of three sources -- direct team,
    winner/loser of another match, or Nth place in a group -- covering round
    robin/groups (direct), single/double-elimination (match-sourced) and
    groups-into-knockout crossover (group-sourced) with one schema. This is a
    three-way choice, more than any existing two-way xor
    ``CheckConstraint`` in this codebase (e.g. ``EventReferee``'s
    member/external_name), so it's enforced in ``clean()`` rather than a
    single airtight DB constraint.
    """

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", _("scheduled")
        IN_PROGRESS = "in_progress", _("in progress")
        COMPLETE = "complete", _("complete")
        CANCELLED = "cancelled", _("cancelled")
        WALKOVER = "walkover", _("walkover")

    stage = models.ForeignKey(TournamentStage, on_delete=models.CASCADE, related_name="matches", verbose_name=_("stage"))
    group = models.ForeignKey(TournamentGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="matches", verbose_name=_("group"))

    round_number = models.PositiveSmallIntegerField(_("round number"), default=1)
    round_label = models.CharField(_("round label"), max_length=100, blank=True, help_text=_('e.g. "Quarterfinal".'))
    sequence = models.PositiveSmallIntegerField(_("sequence"), default=0, help_text=_("Bracket slot order, for rendering."))

    field = models.ForeignKey(TournamentField, on_delete=models.SET_NULL, null=True, blank=True, related_name="matches", verbose_name=_("field"))
    scheduled_start = models.DateTimeField(_("scheduled start"), null=True, blank=True)
    status = models.CharField(_("status"), max_length=15, choices=Status.choices, default=Status.SCHEDULED)

    score_home = models.PositiveSmallIntegerField(_("home score"), null=True, blank=True)
    score_away = models.PositiveSmallIntegerField(_("away score"), null=True, blank=True)
    went_to_overtime = models.BooleanField(_("went to overtime"), default=False)
    penalty_home = models.PositiveSmallIntegerField(_("home penalty score"), null=True, blank=True)
    penalty_away = models.PositiveSmallIntegerField(_("away penalty score"), null=True, blank=True)

    home_team = models.ForeignKey(TournamentTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name="home_matches", verbose_name=_("home team"))
    away_team = models.ForeignKey(TournamentTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name="away_matches", verbose_name=_("away team"))

    home_source_match = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="feeds_home", verbose_name=_("home source match"))
    home_source_is_winner = models.BooleanField(_("home source is winner"), default=True)
    away_source_match = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="feeds_away", verbose_name=_("away source match"))
    away_source_is_winner = models.BooleanField(_("away source is winner"), default=True)

    home_source_group = models.ForeignKey(TournamentGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="feeds_home_rank", verbose_name=_("home source group"))
    home_source_rank = models.PositiveSmallIntegerField(_("home source rank"), null=True, blank=True)
    away_source_group = models.ForeignKey(TournamentGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="feeds_away_rank", verbose_name=_("away source group"))
    away_source_rank = models.PositiveSmallIntegerField(_("away source rank"), null=True, blank=True)

    class Meta:
        verbose_name = _("match")
        verbose_name_plural = _("matches")
        ordering = ["stage", "round_number", "sequence"]

    def __str__(self):
        return f"{self.home_display} vs {self.away_display}"

    def clean(self):
        self._clean_side("home", self.home_team_id, self.home_source_match_id, self.home_source_group_id)
        self._clean_side("away", self.away_team_id, self.away_source_match_id, self.away_source_group_id)

    def _clean_side(self, label, team_id, source_match_id, source_group_id):
        configured = [value is not None for value in (team_id, source_match_id, source_group_id)]
        if sum(configured) > 1:
            raise ValidationError({f"{label}_team": _("Only one of a direct team, a source match, or a source group may be set.")})

    @property
    def home_display(self) -> str:
        return str(self.home_team) if self.home_team_id else "TBD"

    @property
    def away_display(self) -> str:
        return str(self.away_team) if self.away_team_id else "TBD"

    @property
    def winner(self):
        if self.score_home is None or self.score_away is None:
            return None
        if self.score_home > self.score_away:
            return self.home_team
        if self.score_away > self.score_home:
            return self.away_team
        return None  # a draw has no winner/loser to feed forward without penalty scores

    @property
    def loser(self):
        if self.winner is None:
            return None
        return self.away_team if self.winner == self.home_team else self.home_team


class MatchReferee(UUIDModel):
    """One referee assigned to one match -- per-game, not per-tournament, since
    a home tournament typically rotates referees match by match rather than
    fielding one referee for the whole event. Mirrors ``events.EventReferee``'s
    member/external xor exactly. Not itself club-scoped -- its club is
    reached through ``match``."""

    class Status(models.TextChoices):
        INVITED = "invited", _("invited")
        CONFIRMED = "confirmed", _("confirmed")

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="referees", verbose_name=_("match"))
    member = models.ForeignKey("members.Member", on_delete=models.CASCADE, null=True, blank=True, related_name="tournament_referee_assignments", verbose_name=_("member"))
    external_name = models.CharField(_("external referee name"), max_length=255, blank=True)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.CONFIRMED)
    assigned_by = models.ForeignKey("members.Member", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("assigned by"))

    class Meta:
        verbose_name = _("match referee")
        verbose_name_plural = _("match referees")
        ordering = ["match"]
        constraints = [
            models.UniqueConstraint(fields=["match", "member"], condition=Q(member__isnull=False), name="unique_referee_per_match"),
            models.CheckConstraint(
                condition=(Q(member__isnull=False) & Q(external_name="")) | (Q(member__isnull=True) & ~Q(external_name="")),
                name="match_referee_member_xor_external_name",
            ),
        ]

    def __str__(self):
        return f"{self.match} - {self.display_name}"

    @property
    def display_name(self) -> str:
        return str(self.member) if self.member_id else self.external_name


class MatchOfficial(UUIDModel):
    """An off-ice/off-field role for one match (scorekeeper, timekeeper, ...) --
    same shape as ``MatchReferee``, mirroring ``events.EventOfficial`` the same
    way ``MatchReferee`` mirrors ``events.EventReferee``. Not itself
    club-scoped -- its club is reached through ``match``."""

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="officials", verbose_name=_("match"))
    member = models.ForeignKey("members.Member", on_delete=models.CASCADE, null=True, blank=True, related_name="tournament_official_assignments", verbose_name=_("member"))
    external_name = models.CharField(_("external official name"), max_length=255, blank=True)
    role_label = models.CharField(_("role"), max_length=100, blank=True, help_text=_('e.g. "Scorekeeper", "Timekeeper".'))
    assigned_by = models.ForeignKey("members.Member", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("assigned by"))

    class Meta:
        verbose_name = _("match official")
        verbose_name_plural = _("match officials")
        ordering = ["match"]
        constraints = [
            models.UniqueConstraint(fields=["match", "member"], condition=Q(member__isnull=False), name="unique_official_per_match"),
            models.CheckConstraint(
                condition=(Q(member__isnull=False) & Q(external_name="")) | (Q(member__isnull=True) & ~Q(external_name="")),
                name="match_official_member_xor_external_name",
            ),
        ]

    def __str__(self):
        return f"{self.match} - {self.display_name}"

    @property
    def display_name(self) -> str:
        return str(self.member) if self.member_id else self.external_name


class DressingRoom(ClubScopedModel):
    """A club's physical room -- persists across tournaments and seasons, not
    owned by one tournament, same reasoning ``events.Location`` is a
    permanent club record rather than a per-event one."""

    name = models.CharField(_("name"), max_length=255)
    capacity = models.PositiveSmallIntegerField(_("capacity"), null=True, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("dressing room")
        verbose_name_plural = _("dressing rooms")
        ordering = ["name"]

    def __str__(self):
        return self.name


class DressingRoomAssignment(UUIDModel):
    """One team's booking of one dressing room, for part or all of a
    tournament. Double-booking is checked in a service (a capacity-style
    select-for-update lock, the same shape already used for referee
    assignment) rather than a DB constraint, since two overlapping bookings
    of the *same room* for the *same team* are the only thing a UniqueConstraint
    can cheaply express -- true overlap-across-teams needs a query. Not
    itself club-scoped -- its club is reached through ``dressing_room``."""

    dressing_room = models.ForeignKey(DressingRoom, on_delete=models.CASCADE, related_name="assignments", verbose_name=_("dressing room"))
    tournament_team = models.ForeignKey(TournamentTeam, on_delete=models.CASCADE, related_name="dressing_room_assignments", verbose_name=_("tournament team"))
    assigned_from = models.DateTimeField(_("assigned from"), null=True, blank=True)
    assigned_until = models.DateTimeField(_("assigned until"), null=True, blank=True)
    note = models.TextField(_("note"), blank=True)

    class Meta:
        verbose_name = _("dressing room assignment")
        verbose_name_plural = _("dressing room assignments")
        ordering = ["dressing_room", "assigned_from"]
        constraints = [
            models.UniqueConstraint(fields=["dressing_room", "tournament_team"], name="unique_dressing_room_assignment"),
        ]

    def __str__(self):
        return f"{self.dressing_room} - {self.tournament_team}"


class TournamentTask(UUIDModel):
    """A volunteer shift ("Gate duty, Field 2, 09:00-10:30") -- the tournament
    counterpart to ``events.EventTask``'s one-off logistics ask ("bring fruit
    for half-time"), extended with the time and place a shift needs that a
    single-event favour doesn't. Coverage/claim display (family label, not
    the claiming member's own name) and the capacity-locked claim itself
    mirror ``events.EventTask``/``events.services.tasks.claim_task`` exactly
    -- see ``tournaments.services.tasks``. ``is_full``/``claim_labels`` are
    computed by the view layer at render time, same as EventTask's own
    context annotations, not model properties. Not itself club-scoped -- its
    club is reached through ``tournament``."""

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="tasks", verbose_name=_("tournament"))
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    starts_at = models.DateTimeField(_("starts at"))
    ends_at = models.DateTimeField(_("ends at"))
    location_label = models.CharField(_("location"), max_length=255, blank=True, help_text=_('e.g. "Entrance", "Kiosk", "Scoreboard — Field 2".'))
    needed_quantity = models.PositiveSmallIntegerField(_("needed quantity"), default=1, help_text=_("How many people need to cover this shift."))
    created_by = models.ForeignKey("members.Member", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("created by"))
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("tournament task")
        verbose_name_plural = _("tournament tasks")
        ordering = ["tournament", "starts_at"]

    def __str__(self):
        return f"{self.tournament} - {self.title}"

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": _("Must be after the start time.")})


class TournamentTaskClaim(UUIDModel):
    """One person's claim on one slot of a ``TournamentTask`` -- a straight
    mirror of ``events.EventTaskClaim``, capacity-checked the identical way
    (see ``tournaments.services.tasks.claim_task``)."""

    task = models.ForeignKey(TournamentTask, on_delete=models.CASCADE, related_name="claims", verbose_name=_("task"))
    member = models.ForeignKey("members.Member", on_delete=models.CASCADE, related_name="tournament_task_claims", verbose_name=_("member"))
    claimed_at = models.DateTimeField(_("claimed at"), auto_now_add=True)

    class Meta:
        verbose_name = _("tournament task claim")
        verbose_name_plural = _("tournament task claims")
        ordering = ["task", "claimed_at"]
        constraints = [
            models.UniqueConstraint(fields=["task", "member"], name="unique_claim_per_member_per_tournament_task"),
        ]

    def __str__(self):
        return f"{self.task} - {self.member}"
