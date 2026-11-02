import datetime

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField

from members.models import Group, Member
from rosterchief.base import UUIDModel

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
        """Short label built from the start/end years, e.g. "season '25 - '26"."""
        return f"season '{self.start_date:%y} - '{self.end_date:%y}"

    @classmethod
    def get_current(cls, date: datetime.date | None = None):
        """Return the current club's season covering ``date`` (today by default)."""
        if date is None:
            date = timezone.now().date()

        return cls.objects.filter(start_date__lte=date, end_date__gte=date).first()

    @classmethod
    def covering(cls, club, date: datetime.date):
        """Return ``club``'s season covering ``date`` (no tenant context needed)."""
        return cls.objects.filter(club=club, start_date__lte=date, end_date__gte=date).first()

    @classmethod
    def next_after(cls, club, date: datetime.date):
        """Return ``club``'s soonest season starting after ``date`` (no tenant
        context needed) -- the season that follows the one covering ``date``."""
        return cls.objects.filter(club=club, start_date__gt=date).order_by("start_date").first()

    @classmethod
    def before(cls, club, season):
        """Return ``club``'s most recent season starting before ``season`` --
        e.g. the management dashboard's member-count trend compares against
        this. Mirrors next_after's own "adjacent by date" reasoning, just
        looking the other way."""
        return cls.objects.filter(club=club, start_date__lt=season.start_date).order_by("-start_date").first()


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
