import datetime
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField

from club.models import Club, Season
from members.models import Member
from rosterchief.base import ClubScopedModel, UUIDModel, validate_club_scope
from teams.models import Team

#: How long a game is assumed to run when no explicit `end` is given -- set on
#: GAME events at save time (Event.save() below), and reused as a read-time-only
#: fallback for other event kinds by events.services.referees.event_window().
ASSUMED_EVENT_DURATION = datetime.timedelta(hours=2)


class Opponent(ClubScopedModel):
    name = models.CharField(_("name"), max_length=255)
    logo = models.ImageField(_("logo"), upload_to="opponents", blank=True)

    class Meta:
        verbose_name = _("opponent")
        verbose_name_plural = _("opponents")
        ordering = ["name"]

    def __str__(self):
        return self.name


class Location(ClubScopedModel):
    name = models.CharField(_("name"), max_length=255)
    address = models.CharField(_("address"), max_length=255)
    city = models.CharField(_("city"), max_length=255)
    zip_code = models.CharField(_("zip code"), max_length=255)
    country = CountryField(_("country"))
    is_home = models.BooleanField(
        _("home location"),
        default=False,
        help_text=_("The club's own ground, set from the control panel -- lets an event's location tell a home game from an away one."),
    )

    class Meta:
        verbose_name = _("location")
        verbose_name_plural = _("locations")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["club"], condition=Q(is_home=True), name="unique_home_location_per_club"),
        ]

    def __str__(self):
        return self.name


class Event(ClubScopedModel):
    class EventKind(models.TextChoices):
        TRAINING = "training", _("Training")
        GAME = "game", _("Game")
        TOURNAMENT = "tournament", _("Tournament")
        MEETING = "meeting", _("Meeting")
        SOCIAL = "social", _("Social")
        OTHER = "other", _("Other")

    series = models.ForeignKey("EventSeries", on_delete=models.CASCADE, related_name="occurrences", null=True, blank=True, verbose_name=_("series"), help_text=_("The recurring series this occurrence belongs to; blank for one-off events."))
    detached = models.BooleanField(_("detached"), default=False, help_text=_("Edited independently; excluded from series-wide updates and regeneration."))
    cancelled = models.BooleanField(_("cancelled"), default=False)

    teams = models.ManyToManyField(Team, related_name="scheduled_events", blank=True, verbose_name=_("teams"))
    invited_members = models.ManyToManyField(Member, related_name="invited_to_events", blank=True, verbose_name=_("invited members"))
    excluded_members = models.ManyToManyField(Member, related_name="excluded_from_events", blank=True, verbose_name=_("excluded members"))
    season = models.ForeignKey(Season, on_delete=models.SET_NULL, related_name="events", null=True, blank=True, verbose_name=_("season"), help_text=_("Season whose team rosters define the audience; derived from the start date when left blank."))

    kind = models.CharField(_("kind"), max_length=10, choices=EventKind.choices, default=EventKind.OTHER)
    title = models.CharField(_("title"), max_length=255)

    start = models.DateTimeField(_("start"))
    end = models.DateTimeField(_("end"), blank=True, null=True)
    gathering = models.DateTimeField(_("gathering"), blank=True, null=True)
    deadline = models.DateTimeField(_("registration deadline"), blank=True, null=True)

    location = models.ForeignKey(Location, on_delete=models.SET_NULL, related_name="events", null=True, blank=True, verbose_name=_("location"))
    opponent = models.ForeignKey(Opponent, on_delete=models.SET_NULL, related_name="events", null=True, blank=True, verbose_name=_("opponent"))
    created_by = models.ForeignKey(Member, on_delete=models.SET_NULL, related_name="created_events", null=True, blank=True, verbose_name=_("created by"))

    # Game-specific -- meaningless for other kinds, so all optional. external_game_id
    # is this game's id in an external competition/fixture data source, for a later
    # automatic score-fetcher to key off; nothing populates it yet.
    competition = models.CharField(_("competition"), max_length=255, blank=True, help_text=_("The league, cup or competition this game is part of."))
    external_game_id = models.CharField(_("external game ID"), max_length=255, blank=True, help_text=_("This game's id in an external competition data source, for automatic score fetching later."))
    score_for = models.PositiveSmallIntegerField(_("score (us)"), null=True, blank=True)
    score_against = models.PositiveSmallIntegerField(_("score (opponent)"), null=True, blank=True)
    is_live = models.BooleanField(_("live"), default=False, help_text=_("The game is currently in progress."))

    max_referees = models.PositiveSmallIntegerField(_("max referees"), default=2, help_text=_("How many referees can be assigned to this game. Only meaningful for home games -- ignored otherwise."))

    class Meta:
        verbose_name = _("event")
        verbose_name_plural = _("events")
        ordering = ["-start"]

    def __str__(self):
        return self.title

    def clean(self):
        validate_club_scope(self, self.club_id, same_club_fields=("season", "location", "opponent"))

    def save(self, *args, **kwargs):
        if self.kind == self.EventKind.GAME and self.end is None:
            self.end = self.start + ASSUMED_EVENT_DURATION
        super().save(*args, **kwargs)

    @property
    def is_home_game(self) -> bool:
        """Whether this game is being played at the club's own ground
        (Location.is_home) -- False for anything that isn't a game, or a game
        with no location set, or one at an away/neutral location."""
        return self.kind == self.EventKind.GAME and self.location_id is not None and self.location.is_home


class EventSeries(ClubScopedModel):
    """A recurring event definition that materialises concrete Event rows."""

    rrule = models.CharField(_("recurrence rule"), max_length=255, help_text=_("RFC 5545 RRULE, e.g. FREQ=WEEKLY;BYDAY=MO,WE."))
    dtstart = models.DateTimeField(_("first occurrence"))
    until = models.DateTimeField(_("until"), null=True, blank=True, help_text=_("Series end: no occurrences are generated after this. Leave blank for open-ended (bounded by the rule's own COUNT/UNTIL, if any)."))
    duration = models.DurationField(_("duration"), null=True, blank=True, help_text=_("Length of each occurrence; sets each event's end."))
    gathering_offset = models.DurationField(_("gathering offset"), null=True, blank=True, help_text=_("How long before the start each occurrence's gathering time is."))
    deadline_offset = models.DurationField(_("registration deadline offset"), null=True, blank=True, help_text=_("How long before the start each occurrence's registration deadline is."))
    excluded_dates = models.JSONField(_("excluded dates"), default=list, blank=True, help_text=_("ISO start datetimes of occurrences removed from the series (EXDATEs)."))
    generated_until = models.DateTimeField(_("generated until"), null=True, blank=True, help_text=_("Occurrences have been materialised up to this point."))

    # Template copied onto each generated occurrence.
    kind = models.CharField(_("kind"), max_length=10, choices=Event.EventKind.choices, default=Event.EventKind.OTHER)
    title = models.CharField(_("title"), max_length=255)
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, related_name="event_series", null=True, blank=True, verbose_name=_("location"))
    opponent = models.ForeignKey(Opponent, on_delete=models.SET_NULL, related_name="event_series", null=True, blank=True, verbose_name=_("opponent"))
    teams = models.ManyToManyField(Team, related_name="event_series", blank=True, verbose_name=_("teams"))
    invited_members = models.ManyToManyField(Member, related_name="invited_to_event_series", blank=True, verbose_name=_("invited members"))
    excluded_members = models.ManyToManyField(Member, related_name="excluded_from_event_series", blank=True, verbose_name=_("excluded members"))

    class Meta:
        verbose_name = _("event series")
        verbose_name_plural = _("event series")
        ordering = ["title"]

    def __str__(self):
        return self.title

    def clean(self):
        validate_club_scope(self, self.club_id, same_club_fields=("location", "opponent"))


class Attendance(UUIDModel):
    class AttendanceStatus(models.TextChoices):
        PRESENT = "present", _("Present")
        ABSENT = "absent", _("Absent")
        EXCUSED = "excused", _("Excused")
        SELECTED = "selected", _("Selected")
        NOT_SELECTED = "not_selected", _("Not selected")
        MAYBE = "maybe", _("Maybe")
        NO_RESPONSE = "no_response", _("No response")

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="attendances", verbose_name=_("event"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="attendances", verbose_name=_("member"))
    status = models.CharField(_("status"), max_length=20, choices=AttendanceStatus.choices, default=AttendanceStatus.NO_RESPONSE)
    showed_up = models.BooleanField(
        _("showed up"),
        null=True,
        blank=True,
        default=None,
        help_text=_("Recorded by a check-in, separate from the RSVP status above. Blank means no check-in has been recorded yet."),
    )
    note = models.TextField(_("note"), blank=True)

    class Meta:
        verbose_name = _("attendance")
        verbose_name_plural = _("attendances")
        ordering = ["event", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["event", "member"], name="unique_attendance_per_event_per_member"),
        ]

    def __str__(self):
        return f"{self.event} - {self.member}"


class EventReferee(UUIDModel):
    """One referee assigned to one (home) game -- either a club member
    (``member`` set) or an external referee logged by name only
    (``external_name`` set, e.g. a federation-appointed referee the club
    still needs to pay/log) -- never both, never neither. ``assigned_by`` is
    required for now -- assignment is admin-only; a future self-service
    sign-up would make it nullable to mean "the referee signed themself up"
    rather than adding a parallel model. See events.services.referees for the
    home-game gate, the ``Event.max_referees`` capacity ceiling, and the
    (non-blocking) schedule conflict check.

    ``fee``/``km``/``km_rate`` back the referee payment form (PDF export):
    what the club owes this referee for this game, and the mileage rate used
    to compute the travel portion -- snapshotted per assignment (not read
    from a live club-wide setting) so a rate change later doesn't rewrite an
    already-issued form's numbers.
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="referees", verbose_name=_("event"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, null=True, blank=True, related_name="referee_assignments", verbose_name=_("member"))
    external_name = models.CharField(_("external referee name"), max_length=255, blank=True, help_text=_("For a referee who isn't a club member (e.g. federation-appointed) -- logged by name only."))
    assigned_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, related_name="+", verbose_name=_("assigned by"))

    fee = models.DecimalField(_("fee"), max_digits=8, decimal_places=2, default=Decimal("0.00"), blank=True)
    km = models.DecimalField(_("kilometers"), max_digits=6, decimal_places=1, null=True, blank=True)
    km_rate = models.DecimalField(_("rate per km"), max_digits=6, decimal_places=4, null=True, blank=True, help_text=_("Reimbursement rate per kilometer, e.g. 0.4230."))

    class Meta:
        verbose_name = _("event referee")
        verbose_name_plural = _("event referees")
        ordering = ["event", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["event", "member"], name="unique_referee_per_event"),
            models.CheckConstraint(
                condition=(Q(member__isnull=False) & Q(external_name="")) | (Q(member__isnull=True) & ~Q(external_name="")),
                name="event_referee_member_xor_external_name",
            ),
        ]

    def __str__(self):
        return f"{self.event} - {self.display_name}"

    @property
    def display_name(self) -> str:
        return str(self.member) if self.member_id else self.external_name

    @property
    def is_external(self) -> bool:
        return self.member_id is None

    @property
    def km_total(self) -> Decimal:
        return (self.km or Decimal("0")) * (self.km_rate or Decimal("0"))

    @property
    def total_payable(self) -> Decimal:
        return (self.fee or Decimal("0")) + self.km_total


class Competition(models.Model):
    """A competition has a name with a specific URL to fetch data from. These are managed centrally."""

    name = models.CharField(max_length=250)
    module = models.CharField(max_length=250)
    sport_type = models.CharField(
        _("sport"),
        max_length=20,
        choices=Club.SportType.choices,
        default=Club.SportType.OTHER,
        help_text=_("Which sport this competition is for."),
    )
    flag = models.ForeignKey(
        settings.WAFFLE_FLAG_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="competitions",
        verbose_name=_("feature flag"),
        help_text=_("Which clubs this competition is offered to -- set (or leave blank to hide it everywhere) from the control panel's Features page. A competition with no flag never shows up on the Event admin's competition dropdown."),
    )

    class Meta:
        verbose_name = _("competition")
        verbose_name_plural = _("competitions")
        ordering = ["name"]

    def __str__(self):
        return self.name
