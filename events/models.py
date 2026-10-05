import datetime
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField

from club.models import Club, Season
from members.models import Group, Member
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

    # Game-specific -- meaningless for other kinds, so all optional. external_game_id
    # is this game's id in an external competition/fixture data source, for a later
    # automatic score-fetcher to key off; nothing populates it yet.
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
            # Covers both the tenant scope every query already filters on and the
            # -start ordering/range filter (calendars, statistics, dashboards,
            # recurrence, referee/official scheduling) -- Meta.ordering above had
            # no supporting index at all before this.
            models.Index(fields=["club", "start"]),
        ]

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
        indexes = [
            # RSVP screens, coach attendance, lineups, and deadline reminders all
            # filter event+status together -- status itself was unindexed before this.
            models.Index(fields=["event", "status"]),
        ]

    def __str__(self):
        return f"{self.event} - {self.member}"


class Lineup(UUIDModel):
    """A game's line-up -- coach mode's C3 screen (mobile/coach_views.py's
    CoachLineupView): a plain yes/no pick per roster player (``selections``),
    not lines/slots -- a coach doesn't need to think in terms of who's on
    which line, just who's in. Publishing (events.services.lineup.
    publish_lineup) flips every selected member's Attendance.status to
    SELECTED and every other available roster member's to NOT_SELECTED --
    the reason those two statuses exist on Attendance in the first place,
    previously unused."""

    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name="lineup", verbose_name=_("event"))
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="lineups", verbose_name=_("team"))
    published_at = models.DateTimeField(_("published at"), null=True, blank=True)
    scheduled_publish_at = models.DateTimeField(
        _("scheduled publish at"),
        null=True,
        blank=True,
        help_text=_("If set (and not yet published), this line-up publishes itself automatically at this time -- events.tasks.publish_scheduled_lineups is the periodic sweep that catches it."),
    )
    created_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_lineups", verbose_name=_("created by"))

    class Meta:
        verbose_name = _("line-up")
        verbose_name_plural = _("line-ups")

    def __str__(self):
        return f"{self.team} - {self.event}"

    def clean(self):
        validate_club_scope(self, self.event.club_id if self.event_id else None, same_club_fields=("team",))


class LineupSelection(UUIDModel):
    """One roster player the coach has marked "in" for a Lineup -- presence
    of the row *is* the yes; there's no separate flag and no "no" row.
    Grouped by the member's own roster position ("category") when displayed,
    both in coach mode and on the published member-side view -- see
    events.services.lineup for the publish-time read of this set."""

    lineup = models.ForeignKey(Lineup, on_delete=models.CASCADE, related_name="selections", verbose_name=_("line-up"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="lineup_selections", verbose_name=_("member"))

    class Meta:
        verbose_name = _("line-up selection")
        verbose_name_plural = _("line-up selections")
        constraints = [
            models.UniqueConstraint(fields=["lineup", "member"], name="unique_selection_per_lineup_per_member"),
        ]

    def __str__(self):
        return f"{self.lineup} - {self.member}"


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
    no_fee = models.BooleanField(_("no fee"), default=False, help_text=_("Not paid for this game (or doesn't wish to be) -- no fee or travel is owed, and the fee isn't flagged as missing."))

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
    def fee_pending(self) -> bool:
        """No fee entered yet, and not marked as unpaid either."""
        return not self.fee and not self.no_fee

    @property
    def total_payable(self) -> Decimal:
        return (self.fee or Decimal("0")) + self.km_total


class RefereeSignup(UUIDModel):
    """One eligible referee's invite/response for one home game -- the
    self-service counterpart to admin assignment (EventReferee). Created
    automatically (events.services.referees.sync_referee_invites, wired from
    events/signals.py) the moment a home game needs a club-arranged referee,
    for every currently-eligible member (teams.RefereeProfile). Accepting
    (events.services.referees.accept_referee_signup) creates a real
    EventReferee row via the same capacity-checked assign_referee every admin
    assignment goes through (assigned_by=None marks it self-service) -- this
    table only ever tracks the invite/response, never payment/capacity, so
    the desktop referee-management screen stays the single source of truth
    for who's actually refereeing.
    """

    class Status(models.TextChoices):
        INVITED = "invited", _("invited")
        ACCEPTED = "accepted", _("accepted")
        DECLINED = "declined", _("declined")

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="referee_signups", verbose_name=_("event"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="referee_signups", verbose_name=_("member"))
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.INVITED)
    responded_at = models.DateTimeField(_("responded at"), null=True, blank=True)

    class Meta:
        verbose_name = _("referee sign-up")
        verbose_name_plural = _("referee sign-ups")
        ordering = ["event", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["event", "member"], name="unique_referee_signup_per_event_per_member"),
        ]

    def __str__(self):
        return f"{self.event} - {self.member} ({self.status})"


class EventOfficial(UUIDModel):
    """The match-officials counterpart to EventReferee -- same shape, same
    reasoning (see that model's own docstring): one official assigned to one
    (home) game, either a club member or an external one logged by name
    only, never both/neither. See events.services.officials for the
    home-game gate, Event.max_officials, and the (non-blocking) schedule
    conflict check."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="officials", verbose_name=_("event"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, null=True, blank=True, related_name="official_assignments", verbose_name=_("member"))
    external_name = models.CharField(_("external official name"), max_length=255, blank=True, help_text=_("For an official who isn't a club member (e.g. federation-appointed) -- logged by name only."))
    assigned_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, related_name="+", verbose_name=_("assigned by"))

    fee = models.DecimalField(_("fee"), max_digits=8, decimal_places=2, default=Decimal("0.00"), blank=True)
    km = models.DecimalField(_("kilometers"), max_digits=6, decimal_places=1, null=True, blank=True)
    km_rate = models.DecimalField(_("rate per km"), max_digits=6, decimal_places=4, null=True, blank=True, help_text=_("Reimbursement rate per kilometer, e.g. 0.4230."))
    no_fee = models.BooleanField(_("no fee"), default=False, help_text=_("Not paid for this game (or doesn't wish to be) -- no fee or travel is owed, and the fee isn't flagged as missing."))

    class Meta:
        verbose_name = _("event official")
        verbose_name_plural = _("event officials")
        ordering = ["event", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["event", "member"], name="unique_official_per_event"),
            models.CheckConstraint(
                condition=(Q(member__isnull=False) & Q(external_name="")) | (Q(member__isnull=True) & ~Q(external_name="")),
                name="event_official_member_xor_external_name",
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
    def fee_pending(self) -> bool:
        """No fee entered yet, and not marked as unpaid either."""
        return not self.fee and not self.no_fee

    @property
    def total_payable(self) -> Decimal:
        return (self.fee or Decimal("0")) + self.km_total


class OfficialSignup(UUIDModel):
    """The match-officials counterpart to RefereeSignup -- same shape, same
    reasoning: one eligible official's invite/response for one home game,
    created automatically (events.services.officials.sync_official_invites)
    the moment a home game needs a club-arranged official."""

    class Status(models.TextChoices):
        INVITED = "invited", _("invited")
        ACCEPTED = "accepted", _("accepted")
        DECLINED = "declined", _("declined")

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="official_signups", verbose_name=_("event"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="official_signups", verbose_name=_("member"))
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.INVITED)
    responded_at = models.DateTimeField(_("responded at"), null=True, blank=True)

    class Meta:
        verbose_name = _("official sign-up")
        verbose_name_plural = _("official sign-ups")
        ordering = ["event", "member__last_name", "member__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["event", "member"], name="unique_official_signup_per_event_per_member"),
        ]

    def __str__(self):
        return f"{self.event} - {self.member} ({self.status})"


class EventTask(UUIDModel):
    """A logistics ask a team manager/admin attaches to an event (e.g. "Bring
    fruit for half-time") -- families/members confirm whether they can take
    it, up to needed_quantity at once (see EventTaskClaim and
    events.services.tasks.claim_task for the capacity-checked claim itself).
    Not kind-restricted -- a training or social can need this just as much
    as a game."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="tasks", verbose_name=_("event"))
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    needed_quantity = models.PositiveSmallIntegerField(_("needed quantity"), default=1, help_text=_("How many people/families need to take this on."))
    created_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("created by"))
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("event task")
        verbose_name_plural = _("event tasks")
        ordering = ["event", "created_at"]

    def __str__(self):
        return f"{self.event} - {self.title}"


class EventTaskClaim(UUIDModel):
    """One person's claim on one slot of an EventTask -- see
    events.services.tasks.claim_task for the capacity check against
    EventTask.needed_quantity. Displayed under the claiming member's own
    family (members.services.family.claim_label_for) rather than their own
    name, when they have one -- the rest of the team only needs to know it's
    covered, not by which specific parent or child."""

    task = models.ForeignKey(EventTask, on_delete=models.CASCADE, related_name="claims", verbose_name=_("task"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="event_task_claims", verbose_name=_("member"))
    claimed_at = models.DateTimeField(_("claimed at"), auto_now_add=True)

    class Meta:
        verbose_name = _("event task claim")
        verbose_name_plural = _("event task claims")
        ordering = ["task", "claimed_at"]
        constraints = [
            models.UniqueConstraint(fields=["task", "member"], name="unique_claim_per_member_per_task"),
        ]

    def __str__(self):
        return f"{self.task} - {self.member}"


class GameTimeline(UUIDModel):
    """What happened during one game -- goals and who scored them, penalties,
    goalie changes, time-outs, period scores -- as fetched from the game's
    competition (see GAME_TIMELINE.md and events.services.game_timeline).

    A one-to-one side table rather than columns on Event: Event is loaded
    everywhere (calendar, lists, attendance, the API) and ``data`` runs from a
    few to a few dozen KB, so it's only joined where it's actually wanted.
    Club-scoped through ``event``, like Attendance/Lineup/EventReferee.

    ``data`` is the normalized document (GAME_TIMELINE.md §2.2) -- one shape
    for every source. ``data_hash`` lets a poll that found nothing new skip the
    write. ``raw`` is the last payload ``data`` was parsed from, kept (only the
    latest) so a parser fix can re-parse stored games.
    """

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

    class Meta:
        verbose_name = _("game timeline")
        verbose_name_plural = _("game timelines")

    def __str__(self):
        return f"{self.event} ({self.status})"


class GameMoment(UUIDModel):
    """One piece of news detected in a game's timeline, from the club's own
    side -- a goal for or against, a goal taken back, a penalty, a period or
    the game ending (events.services.game_moments, GAME_TIMELINE.md §4).

    Recorded once per (event, key): ``key`` is the kind plus the timeline event
    it's about, so a repeated or racing fetch can't record it twice. Push
    notifications (not built yet) will read these rows -- ``notifiable`` says
    whether it's recent enough to be worth telling anyone, ``delivered_at``
    will say it was.
    """

    class Kind(models.TextChoices):
        GAME_STARTED = "game_started", _("game started")
        GOAL_FOR = "goal_for", _("goal for")
        GOAL_AGAINST = "goal_against", _("goal against")
        GOAL_CANCELLED = "goal_cancelled", _("goal cancelled")
        PENALTY_FOR = "penalty_for", _("penalty for")
        PENALTY_AGAINST = "penalty_against", _("penalty against")
        PENALTY_SHOT_FOR = "penalty_shot_for", _("penalty shot for")
        PENALTY_SHOT_AGAINST = "penalty_shot_against", _("penalty shot against")
        PERIOD_ENDED = "period_ended", _("period ended")
        GAME_FINAL = "game_final", _("game final")

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="moments", verbose_name=_("event"))
    key = models.CharField(_("key"), max_length=255)
    kind = models.CharField(_("kind"), max_length=30, choices=Kind.choices)
    payload = models.JSONField(_("payload"), default=dict, blank=True)
    game_seconds = models.PositiveIntegerField(_("game time (seconds)"), null=True, blank=True)
    notifiable = models.BooleanField(_("notifiable"), default=True, help_text=_("Recent enough in game time to be worth notifying about (not a catch-up after an outage)."))
    created_at = models.DateTimeField(_("detected at"), auto_now_add=True)
    delivered_at = models.DateTimeField(_("delivered at"), null=True, blank=True, help_text=_("When it was pushed out -- not used until push notifications exist."))

    class Meta:
        verbose_name = _("game moment")
        verbose_name_plural = _("game moments")
        ordering = ["event", "created_at"]
        constraints = [models.UniqueConstraint(fields=["event", "key"], name="unique_game_moment_per_event")]

    def __str__(self):
        return f"{self.event} - {self.get_kind_display()}"


class GameFollow(ClubScopedModel):
    """An account following one of the club's teams for live game pushes --
    goals, penalties, the result, as detected in its games' timelines
    (events.services.game_push, GAME_TIMELINE.md phase 4). Opt-in: anyone with
    an account in the club picks the teams they want, from the member app or
    the PWA's "Live game updates" screen. Per account (not per Member), since
    pushes go to an account's devices whichever of its people they concern."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_follows", verbose_name=_("user"))
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="followers", verbose_name=_("team"))

    class Meta:
        verbose_name = _("game follow")
        verbose_name_plural = _("game follows")
        constraints = [models.UniqueConstraint(fields=["user", "team"], name="unique_game_follow_per_team")]

    def __str__(self):
        return f"{self.user} -> {self.team}"


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
