import datetime
import secrets
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import get_valid_filename
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField

from members.models import Group, Member
from rosterchief.base import UUIDModel
from rosterchief.storage import get_private_storage

ASSUMED_EVENT_DURATION = datetime.timedelta(hours=2)


class Opponent(UUIDModel):
    name = models.CharField(_("name"), max_length=255)
    logo = models.ImageField(_("logo"), upload_to="opponents", blank=True)
    alternative_names = models.JSONField(_("alternative names"), default=list, blank=True, editable=False)

    class Meta:
        verbose_name = _("opponent")
        verbose_name_plural = _("opponents")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def knows_name(self, name: str) -> bool:
        """Whether ``name`` is this opponent's name or a recorded alternative (case-insensitive)."""
        wanted = name.strip().casefold()
        return wanted in {known.casefold() for known in [self.name, *self.alternative_names]}

    def remember_name(self, name: str) -> bool:
        """Records ``name`` as an alternative spelling unless already known. True if saved."""
        name = name.strip()
        if not name or self.knows_name(name):
            return False
        self.alternative_names = [*self.alternative_names, name]
        self.save(update_fields=["alternative_names"])
        return True


class Season(UUIDModel):
    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"))

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("season")
        verbose_name_plural = _("seasons")

    @property
    def name(self):
        """Short label built from the start/end years, e.g. "Season '25 - '26"."""
        return f"Season '{self.start_date:%y} - '{self.end_date:%y}"

    @classmethod
    def get_current(cls, date: datetime.date | None = None):
        """Return the current club's season covering ``date`` (today by default)."""
        if date is None:
            date = timezone.now().date()

        return cls.objects.filter(start_date__lte=date, end_date__gte=date).first()

    @classmethod
    def covering(cls, club, date: datetime.date):
        """Return ``club``'s season covering ``date`` (no tenant context needed)."""
        return cls.objects.filter(start_date__lte=date, end_date__gte=date).first()

    @classmethod
    def next_after(cls, club, date: datetime.date):
        """Return ``club``'s soonest season starting after ``date`` (no tenant
        context needed) -- the season that follows the one covering ``date``."""
        return cls.objects.filter(start_date__gt=date).order_by("start_date").first()

    @classmethod
    def before(cls, club, season):
        """Return ``club``'s most recent season starting before ``season`` --
        e.g. the management dashboard's member-count trend compares against
        this. Mirrors next_after's own "adjacent by date" reasoning, just
        looking the other way."""
        return cls.objects.filter(start_date__lt=season.start_date).order_by("-start_date").first()


class Location(UUIDModel):
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

    latitude = models.FloatField(
        _("latitude"),
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
        help_text=_("Optional exact position, set from the control panel for the home location. When blank, maps look the address up."),
    )
    longitude = models.FloatField(_("longitude"), null=True, blank=True, validators=[MinValueValidator(-180), MaxValueValidator(180)])

    class Meta:
        verbose_name = _("location")
        verbose_name_plural = _("locations")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValidationError({"latitude" if self.latitude is None else "longitude": _("Give both latitude and longitude, or neither.")})


class Event(UUIDModel):
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

    teams = models.ManyToManyField("teams.Team", related_name="scheduled_events", blank=True, verbose_name=_("teams"))
    groups = models.ManyToManyField(Group, related_name="scheduled_events", blank=True, verbose_name=_("groups"), help_text=_("Invite every current member of these groups."))
    club_wide = models.BooleanField(_("whole club"), default=False, help_text=_("Invite every active club member for this event's season, instead of specific teams/groups. Can't be combined with teams or groups."))
    invited_members = models.ManyToManyField(Member, related_name="invited_to_events", blank=True, verbose_name=_("invited members"))
    excluded_members = models.ManyToManyField(Member, related_name="excluded_from_events", blank=True, verbose_name=_("excluded members"))
    season = models.ForeignKey(Season, on_delete=models.SET_NULL, related_name="events", null=True, blank=True, verbose_name=_("season"), help_text=_("Season whose team rosters define the audience; derived from the start date when left blank."))

    kind = models.CharField(_("kind"), max_length=10, choices=EventKind.choices, default=EventKind.OTHER)
    subtype = models.ForeignKey("EventSubtype", on_delete=models.SET_NULL, related_name="events", null=True, blank=True, verbose_name=_("subtype"))
    title = models.CharField(_("title"), max_length=255)

    start = models.DateTimeField(_("start"))
    end = models.DateTimeField(_("end"), blank=True, null=True)
    gathering = models.DateTimeField(_("gathering"), blank=True, null=True)
    deadline = models.DateTimeField(_("registration deadline"), blank=True, null=True)

    location = models.ForeignKey(Location, on_delete=models.SET_NULL, related_name="events", null=True, blank=True, verbose_name=_("location"))
    opponent = models.ForeignKey(Opponent, on_delete=models.SET_NULL, related_name="events", null=True, blank=True, verbose_name=_("opponent"))
    created_by = models.ForeignKey(Member, on_delete=models.SET_NULL, related_name="created_events", null=True, blank=True, verbose_name=_("created by"))

    competition = models.CharField(_("competition"), max_length=255, blank=True, help_text=_("The league, cup or competition this game is part of."))
    competition_label = models.CharField(
        _("competition label"),
        max_length=100,
        blank=True,
        help_text=_("Tells apart multiple competitions a team plays in via the same source, e.g. “Division 1” vs “Cup” -- purely a display label, unlike competition itself, which fetch_game_info matches against a Competition row's name."),
    )
    is_friendly = models.BooleanField(_("friendly"), default=False, help_text=_("A friendly game doesn't count towards the standings."))
    external_game_id = models.CharField(_("external game ID"), max_length=255, blank=True, help_text=_("This game's id in an external competition data source, for automatic score fetching later."))
    external_source_id = models.CharField(
        _("external source ID"),
        max_length=255,
        blank=True,
        help_text=_("Which of that data source's own entries this game came from -- e.g. RBIHF's own team id for the page it was scraped from. Set by the importer; scopes re-imports so one competition never deletes another's games."),
    )
    score_for = models.PositiveSmallIntegerField(_("score (us)"), null=True, blank=True)
    score_against = models.PositiveSmallIntegerField(_("score (opponent)"), null=True, blank=True)
    is_live = models.BooleanField(_("live"), default=False, help_text=_("The game is currently in progress."))
    live_score_polling_done_at = models.DateTimeField(
        _("live score polling done at"),
        null=True,
        blank=True,
        help_text=_("Set once the per-minute score poller has seen this game go live and then come back off live -- i.e. it's finished, so there's no point calling the data source again for the rest of the polling window."),
    )

    max_referees = models.PositiveSmallIntegerField(_("max referees"), default=2, help_text=_("How many referees can be assigned to this game. Only meaningful for a home game/tournament -- ignored otherwise."))
    max_officials = models.PositiveSmallIntegerField(_("max officials"), default=1, help_text=_("How many match officials can be assigned to this game. Only meaningful for a home game/tournament -- ignored otherwise."))

    deadline_reminder_sent_at = models.DateTimeField(
        _("deadline reminder sent at"),
        null=True,
        blank=True,
        help_text=_("Set once events.tasks.send_deadline_reminders has nudged whoever still hasn't answered -- keeps that job from reminding the same event twice."),
    )

    class Meta:
        verbose_name = _("event")
        verbose_name_plural = _("events")
        ordering = ["-start"]
        indexes = [
            models.Index(fields=["start"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.kind == self.EventKind.GAME and self.end is None:
            self.end = self.start + ASSUMED_EVENT_DURATION
        super().save(*args, **kwargs)

    @property
    def is_home_game(self) -> bool:
        """Whether this game is being played at the club's own ground
        (Location.is_home) -- False for anything that isn't a game, or a game
        with no location set, or one at an away/neutral location. Drives
        score/opponent semantics (events/api.py, events/competition/hockey.py),
        which only make sense for a Game -- see is_home_fixture below for the
        broader "referees/officials might be needed here" check, which also
        covers a home Tournament."""
        return self.kind == self.EventKind.GAME and self.location_id is not None and self.location.is_home

    @property
    def is_home_fixture(self) -> bool:
        """Whether this is a Game or Tournament at the club's own ground --
        the referee/official tools' own notion of "home", broader than
        is_home_game above: a home tournament needs officiating arranged
        just as much as a home game does, even though it has no single
        opponent or score of its own. See events.services.referees.
        needs_referee_management/events.services.officials.
        needs_official_management, the only two callers."""
        return self.kind in (self.EventKind.GAME, self.EventKind.TOURNAMENT) and self.location_id is not None and self.location.is_home


class EventSubtype(UUIDModel):
    """A club-defined flavour of an event kind ("Fitness training", "Friendly game", ...), picked in the event form.
    ``title_template`` suggests the title: for a training / game it is what a blank title becomes, for other kinds only
    the placeholder in the (still required) title box. Placeholders: ``{teams}`` (short names), ``{opponent}``, ``{kind}``."""

    kind = models.CharField(_("kind"), max_length=10, choices=Event.EventKind.choices)
    name = models.CharField(_("name"), max_length=100)
    title_template = models.CharField(_("title template"), max_length=255, blank=True, help_text=_("Suggested title; may use {teams}, {opponent} and {kind}."))
    ordering = models.PositiveSmallIntegerField(_("ordering"), default=0)

    class Meta:
        verbose_name = _("event subtype")
        verbose_name_plural = _("event subtypes")
        ordering = ["kind", "ordering", "name"]
        constraints = [models.UniqueConstraint(fields=["kind", "name"], name="unique_event_subtype_name_per_kind")]

    def __str__(self):
        return self.name


class _Assignment(UUIDModel):
    """One referee / official on one (home) game: a club member (``member``) or an external person logged by name only
    (``external_name``), never both and never neither. ``fee``/``km``/``km_rate`` back the payment form: what the club owes
    for this game, with the mileage rate snapshotted per assignment so a later rate change never rewrites an issued form."""

    external_name = models.CharField(_("external name"), max_length=255, blank=True, help_text=_("For someone who isn't a club member (e.g. federation-appointed), logged by name only."))
    assigned_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("assigned by"))
    referee_type = models.ForeignKey("club.RefereeType", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("type"))

    fee = models.DecimalField(_("fee"), max_digits=8, decimal_places=2, default=Decimal("0.00"), blank=True)
    km = models.DecimalField(_("kilometers"), max_digits=6, decimal_places=1, null=True, blank=True)
    km_rate = models.DecimalField(_("rate per km"), max_digits=6, decimal_places=4, null=True, blank=True, help_text=_("Reimbursement rate per kilometer, e.g. 0.4230."))
    no_fee = models.BooleanField(_("no fee"), default=False, help_text=_("Not paid for this game: no fee or travel is owed and the fee is not flagged as missing."))

    class Meta:
        abstract = True
        ordering = ["event", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["event", "member"], name="unique_%(class)s_per_event"),
            models.CheckConstraint(condition=(Q(member__isnull=False) & Q(external_name="")) | (Q(member__isnull=True) & ~Q(external_name="")), name="%(class)s_member_xor_external_name"),
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
    def fee_pending(self) -> bool:
        """No fee entered yet, and not marked as unpaid either."""
        return not self.fee and not self.no_fee

    @property
    def total_payable(self) -> Decimal:
        return Decimal("0") if self.no_fee else (self.fee or Decimal("0")) + self.km_total


class EventReferee(_Assignment):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="referees", verbose_name=_("event"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, null=True, blank=True, related_name="referee_assignments", verbose_name=_("member"))

    class Meta(_Assignment.Meta):
        verbose_name = _("event referee")
        verbose_name_plural = _("event referees")


class EventOfficial(_Assignment):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="officials", verbose_name=_("event"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, null=True, blank=True, related_name="official_assignments", verbose_name=_("member"))

    class Meta(_Assignment.Meta):
        verbose_name = _("event official")
        verbose_name_plural = _("event officials")


class _Signup(UUIDModel):
    """A member who offered to referee / officiate a game. Interest only: it takes no place until staff assign the person,
    which removes the sign-up."""

    class Meta:
        abstract = True
        ordering = ["created"]
        constraints = [models.UniqueConstraint(fields=["event", "member"], name="unique_%(class)s_per_event")]

    def __str__(self):
        return f"{self.event} - {self.member}"


class RefereeSignup(_Signup):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="referee_signups", verbose_name=_("event"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="referee_signups", verbose_name=_("member"))

    class Meta(_Signup.Meta):
        verbose_name = _("referee sign-up")
        verbose_name_plural = _("referee sign-ups")


class OfficialSignup(_Signup):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="official_signups", verbose_name=_("event"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="official_signups", verbose_name=_("member"))

    class Meta(_Signup.Meta):
        verbose_name = _("official sign-up")
        verbose_name_plural = _("official sign-ups")


class OfficiatingInvitation(UUIDModel):
    """Keep declines and notification history separate from offers and assignments."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="officiating_invitations")
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="officiating_invitations")
    kind = models.CharField(max_length=8, choices=[("referee", _("referee")), ("official", _("official"))])
    declined = models.BooleanField(default=False)
    notified = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "member", "kind"], name="unique_officiating_invitation")]


class EventSeries(UUIDModel):
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
    subtype = models.ForeignKey("EventSubtype", on_delete=models.SET_NULL, related_name="event_series", null=True, blank=True, verbose_name=_("subtype"))
    title = models.CharField(_("title"), max_length=255)
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, related_name="event_series", null=True, blank=True, verbose_name=_("location"))
    opponent = models.ForeignKey(Opponent, on_delete=models.SET_NULL, related_name="event_series", null=True, blank=True, verbose_name=_("opponent"))
    teams = models.ManyToManyField("teams.Team", related_name="event_series", blank=True, verbose_name=_("teams"))
    groups = models.ManyToManyField(Group, related_name="event_series", blank=True, verbose_name=_("groups"), help_text=_("Invite every current member of these groups."))
    club_wide = models.BooleanField(_("whole club"), default=False, help_text=_("Invite every active club member for each occurrence's season, instead of specific teams/groups. Can't be combined with teams or groups."))
    invited_members = models.ManyToManyField(Member, related_name="invited_to_event_series", blank=True, verbose_name=_("invited members"))
    excluded_members = models.ManyToManyField(Member, related_name="excluded_from_event_series", blank=True, verbose_name=_("excluded members"))

    class Meta:
        verbose_name = _("event series")
        verbose_name_plural = _("event series")
        ordering = ["title"]

    def __str__(self):
        return self.title


class Attendance(UUIDModel):
    """One invited member's answer for one event: the RSVP/coach status, the check-in (``showed_up``) and notes."""

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
    coach_note = models.TextField(
        _("coach note"),
        blank=True,
        help_text=_("Private to the team's managers: never shown to the player or their guardians, in any app or notification."),
    )
    answered_by_coach = models.BooleanField(_("answered by a coach"), default=False, help_text=_("The current answer was set by a coach for the player. A reply from the player themselves, or a reset, clears this."))
    coach_set_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("last changed by coach"))
    coach_set_at = models.DateTimeField(_("last changed by a coach at"), null=True, blank=True)

    class Meta:
        verbose_name = _("attendance")
        verbose_name_plural = _("attendances")
        ordering = ["event", "member__last_name", "member__first_name"]
        constraints = [models.UniqueConstraint(fields=["event", "member"], name="unique_attendance_per_event_per_member")]
        indexes = [models.Index(fields=["event", "status"])]

    def __str__(self):
        return f"{self.event} - {self.member}"


class AttendanceChange(UUIDModel):
    """One change of an attendance's status: who set it (a coach, the player, a parent/guardian or the system), when,
    and from what to what. ``changed_by_name`` is a snapshot so the log survives a deleted account."""

    class Via(models.TextChoices):
        COACH = "coach", _("Coach")
        SELF = "self", _("The player")
        GUARDIAN = "guardian", _("Parent / guardian")
        SYSTEM = "system", _("System")

    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, related_name="changes", verbose_name=_("attendance"))
    from_status = models.CharField(_("from"), max_length=20, choices=Attendance.AttendanceStatus.choices)
    to_status = models.CharField(_("to"), max_length=20, choices=Attendance.AttendanceStatus.choices)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("changed by"))
    changed_by_name = models.CharField(_("changed by (name)"), max_length=255, blank=True)
    via = models.CharField(_("via"), max_length=10, choices=Via.choices, default=Via.COACH)
    note_changed = models.BooleanField(_("note changed"), default=False)

    class Meta:
        verbose_name = _("attendance change")
        verbose_name_plural = _("attendance changes")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.attendance}: {self.from_status} -> {self.to_status}"


class Lineup(UUIDModel):
    """A game's selection: a plain yes/no pick per player (``selections``), not lines or slots. Publishing flips every
    selected member's attendance to SELECTED and every other available member's to NOT_SELECTED."""

    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name="lineup", verbose_name=_("event"))
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, related_name="lineups", verbose_name=_("team"))
    published_at = models.DateTimeField(_("published at"), null=True, blank=True)
    scheduled_publish_at = models.DateTimeField(
        _("scheduled publish at"),
        null=True,
        blank=True,
        help_text=_("If set (and not yet published), this line-up publishes itself at this time: a periodic task sweeps for due ones."),
    )
    created_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_lineups", verbose_name=_("created by"))

    class Meta:
        verbose_name = _("line-up")
        verbose_name_plural = _("line-ups")

    def __str__(self):
        return f"{self.team} - {self.event}"


class LineupSelection(UUIDModel):
    """One player the coach marked "in" for a line-up: the row's presence is the yes, there is no "no" row."""

    lineup = models.ForeignKey(Lineup, on_delete=models.CASCADE, related_name="selections", verbose_name=_("line-up"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="lineup_selections", verbose_name=_("member"))

    class Meta:
        verbose_name = _("line-up selection")
        verbose_name_plural = _("line-up selections")
        constraints = [models.UniqueConstraint(fields=["lineup", "member"], name="unique_selection_per_lineup_per_member")]

    def __str__(self):
        return f"{self.lineup} - {self.member}"


class EventTask(UUIDModel):
    """A logistics ask a team manager attaches to an event (e.g. "Bring fruit for half-time"): members confirm they can
    take it, up to ``needed_quantity`` at once (see EventTaskClaim). Any kind of event can have tasks."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="tasks", verbose_name=_("event"))
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    needed_quantity = models.PositiveSmallIntegerField(_("needed quantity"), default=1, help_text=_("How many people/families need to take this on."))
    created_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("created by"))

    class Meta:
        verbose_name = _("event task")
        verbose_name_plural = _("event tasks")
        ordering = ["event", "created"]

    def __str__(self):
        return f"{self.event} - {self.title}"


class EventTaskClaim(UUIDModel):
    """One person's claim on one slot of an EventTask (capacity is checked against ``needed_quantity``)."""

    task = models.ForeignKey(EventTask, on_delete=models.CASCADE, related_name="claims", verbose_name=_("task"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="event_task_claims", verbose_name=_("member"))

    class Meta:
        verbose_name = _("event task claim")
        verbose_name_plural = _("event task claims")
        ordering = ["task", "created"]
        constraints = [models.UniqueConstraint(fields=["task", "member"], name="unique_claim_per_member_per_task")]

    def __str__(self):
        return f"{self.task} - {self.member}"


def event_attachment_path(attachment, filename):
    """``event-attachments/<event id>/<random>-<safe file name>`` in private storage: the random prefix keeps same-named
    uploads apart and the path unguessable; the original name is kept (made filesystem-safe) so a download is recognisable."""
    try:
        safe_name = get_valid_filename(filename)
    except SuspiciousFileOperation:
        safe_name = "file"
    if len(safe_name) > 100:
        stem, dot, extension = safe_name.rpartition(".")
        safe_name = f"{stem[: 100 - len(extension) - 1]}.{extension}" if dot and len(extension) < 10 else safe_name[:100]
    return f"event-attachments/{attachment.event_id}/{secrets.token_hex(8)}-{safe_name}"


class EventAttachment(UUIDModel):
    """A file attached to an event (a tournament schedule, a map, a consent form) for the invited people to open. Stored
    privately and only ever streamed through an authenticated view, never a public /media link."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="attachments", verbose_name=_("event"))
    file = models.FileField(_("file"), storage=get_private_storage, upload_to=event_attachment_path, max_length=255)
    name = models.CharField(_("name"), max_length=255, help_text=_("The file name as uploaded -- what a download is called."))
    size = models.PositiveIntegerField(_("size"), default=0, help_text=_("Bytes."))
    uploaded_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("uploaded by"))

    class Meta:
        verbose_name = _("event attachment")
        verbose_name_plural = _("event attachments")
        ordering = ["event", "created"]

    def __str__(self):
        return f"{self.event} - {self.name}"


class GameTimeline(UUIDModel):
    """What happened during one game (goals and who scored them, penalties, goalie changes, time-outs, period scores) as
    fetched from the game's competition. A one-to-one side table: ``data`` is the normalised document (one shape for every
    source), ``data_hash`` lets a poll that found nothing new skip the write, ``raw`` is the last payload it was parsed
    from so a parser fix can re-parse stored games."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", _("scheduled")
        LIVE = "live", _("live")
        FINAL = "final", _("final")

    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name="timeline", verbose_name=_("event"))
    source = models.CharField(_("source"), max_length=50, help_text=_("The competition this was fetched from (Event.competition at fetch time)."))
    data = models.JSONField(_("data"), default=dict, blank=True)
    data_hash = models.CharField(_("data hash"), max_length=64, blank=True)
    raw = models.TextField(_("raw payload"), blank=True)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.SCHEDULED, db_index=True)
    fetched_at = models.DateTimeField(_("fetched at"), null=True, blank=True, help_text=_("Last successful fetch."))
    changed_at = models.DateTimeField(_("changed at"), null=True, blank=True, help_text=_("Last time the timeline actually changed."))
    finalized_at = models.DateTimeField(_("finalized at"), null=True, blank=True, help_text=_("Set once a finished game's timeline stopped changing; it isn't re-fetched after that."))
    last_error = models.TextField(_("last error"), blank=True, help_text=_("The last fetch/parse error, if the latest attempt failed; the previous data is kept."))
    strength_overrides = models.JSONField(
        _("strength overrides"), default=dict, blank=True, help_text=_("Goal strengths staff corrected or confirmed, {timeline event id: strength}. Applied to the game's stats, and kept when the timeline is fetched again.")
    )

    class Meta:
        verbose_name = _("game timeline")
        verbose_name_plural = _("game timelines")

    def __str__(self):
        return f"{self.event} ({self.status})"


class GameMoment(UUIDModel):
    """One piece of news detected in a game's timeline from the club's own side (a goal for or against, a penalty, a period
    or the game ending). Recorded once per (event, key) so a repeated or racing fetch cannot record it twice; ``notifiable``
    says it is recent enough to be worth telling anyone, ``delivered_at`` that it was pushed."""

    class Kind(models.TextChoices):
        GAME_STARTED = "game_started", _("game started")
        GOAL_FOR = "goal_for", _("goal for")
        GOAL_AGAINST = "goal_against", _("goal against")
        GOAL_CANCELLED = "goal_cancelled", _("goal cancelled")
        PENALTY_FOR = "penalty_for", _("penalty for")
        PENALTY_AGAINST = "penalty_against", _("penalty against")
        PENALTY_SHOT_FOR = "penalty_shot_for", _("penalty shot for")
        PENALTY_SHOT_AGAINST = "penalty_shot_against", _("penalty shot against")
        PERIOD_STARTED = "period_started", _("period started")
        PERIOD_ENDED = "period_ended", _("period ended")
        GAME_FINAL = "game_final", _("game final")

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="moments", verbose_name=_("event"))
    key = models.CharField(_("key"), max_length=255)
    kind = models.CharField(_("kind"), max_length=30, choices=Kind.choices)
    payload = models.JSONField(_("payload"), default=dict, blank=True)
    game_seconds = models.PositiveIntegerField(_("game time (seconds)"), null=True, blank=True)
    notifiable = models.BooleanField(_("notifiable"), default=True, help_text=_("Recent enough in game time to be worth notifying about (not a catch-up after an outage)."))
    delivered_at = models.DateTimeField(_("delivered at"), null=True, blank=True, help_text=_("When it was pushed out."))

    class Meta:
        verbose_name = _("game moment")
        verbose_name_plural = _("game moments")
        ordering = ["event", "created"]
        constraints = [models.UniqueConstraint(fields=["event", "key"], name="unique_game_moment_per_event")]

    def __str__(self):
        return f"{self.event} - {self.get_kind_display()}"


class GameStat(UUIDModel):
    """One countable thing a player (or the opposition) did in a game (a goal, an assist, penalty minutes), flattened out of
    the game's timeline so team analytics can sum it with a query. Sport-agnostic: ``stat_type`` is a free slug and ``value``
    how much this row counts. One row per (game, team of ours, source event, stat type); ``is_ours`` is resolved from the
    club's own side; ``member`` is matched to the roster when the jersey number or name allows."""

    class StatType(models.TextChoices):
        GOAL = "goal", _("goal")
        ASSIST = "assist", _("assist")
        PENALTY_MINUTES = "penalty_minutes", _("penalty minutes")
        SHOT = "shot", _("shot on goal")
        POWER_PLAY_OPPORTUNITY = "pp_opportunity", _("power-play opportunity")
        GOAL_CONCEDED = "goal_conceded", _("goal conceded")
        GOALIE_SECONDS = "goalie_seconds", _("goalie time in net (seconds)")
        APPEARANCE = "appearance", _("game played (on the line-up)")

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="stats", verbose_name=_("event"))
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, related_name="game_stats", verbose_name=_("team"))
    season = models.ForeignKey(Season, on_delete=models.CASCADE, null=True, blank=True, related_name="game_stats", verbose_name=_("season"))
    stat_type = models.CharField(_("stat"), max_length=30, db_index=True)
    value = models.PositiveSmallIntegerField(_("value"), default=1)
    is_ours = models.BooleanField(_("our side"), help_text=_("Whether the club's own side did this (False: the opposition)."))
    member = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="game_stats", verbose_name=_("member"))
    player_name = models.CharField(_("player name"), max_length=255, blank=True)
    player_number = models.PositiveSmallIntegerField(_("player number"), null=True, blank=True)
    period = models.CharField(_("period"), max_length=10, blank=True)
    game_seconds = models.PositiveIntegerField(_("game time (seconds)"), null=True, blank=True)
    source_key = models.CharField(_("source key"), max_length=255, help_text=_("The timeline event this came from (plus the slot, for several rows from one event)."))
    meta = models.JSONField(_("details"), default=dict, blank=True)

    class Meta:
        verbose_name = _("game stat")
        verbose_name_plural = _("game stats")
        indexes = [models.Index(fields=["team", "season", "stat_type"])]
        constraints = [models.UniqueConstraint(fields=["event", "team", "stat_type", "source_key"], name="unique_game_stat_per_source")]

    def __str__(self):
        return f"{self.event} - {self.stat_type} {self.value}"


class GameFollow(UUIDModel):
    """An account following one of the club's teams for live game pushes (goals, penalties, the result). Opt-in, per
    account (not per member) since pushes go to an account's devices."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_follows", verbose_name=_("user"))
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, related_name="followers", verbose_name=_("team"))

    class Meta:
        verbose_name = _("game follow")
        verbose_name_plural = _("game follows")
        constraints = [models.UniqueConstraint(fields=["user", "team"], name="unique_game_follow_per_team")]

    def __str__(self):
        return f"{self.user} -> {self.team}"


class EventFollow(UUIDModel):
    """An account following ONE game for live updates; the single-game counterpart to GameFollow."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_follows", verbose_name=_("user"))
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="followers", verbose_name=_("event"))

    class Meta:
        verbose_name = _("followed game")
        verbose_name_plural = _("followed games")
        constraints = [models.UniqueConstraint(fields=["user", "event"], name="unique_event_follow_per_game")]

    def __str__(self):
        return f"{self.user} -> {self.event}"


class StatsRefresh(UUIDModel):
    """A background re-link of game stats after someone was added to a team's roster: the team's stored game sheets are
    matched against the new roster again (jersey number / name) so the player's numbers show up. While ``running`` the
    member's Stats tab shows an "update in progress" banner."""

    class Status(models.TextChoices):
        RUNNING = "running", _("running")
        DONE = "done", _("done")
        FAILED = "failed", _("failed")

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="stats_refreshes", verbose_name=_("member"))
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, related_name="stats_refreshes", verbose_name=_("team"))
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.RUNNING, db_index=True)
    games = models.PositiveIntegerField(_("games checked"), default=0)
    updated = models.PositiveIntegerField(_("games updated"), default=0)
    finished_at = models.DateTimeField(_("finished at"), null=True, blank=True)
    error = models.TextField(_("error"), blank=True)

    class Meta:
        verbose_name = _("stats refresh")
        verbose_name_plural = _("stats refreshes")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.member} / {self.team} ({self.status})"
