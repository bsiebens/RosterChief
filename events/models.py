from django.db import models
from django.utils.translation import gettext_lazy as _

from club.models import Season
from members.models import Member
from rosterchief.base import ClubScopedModel, UUIDModel, validate_club_scope
from teams.models import Team


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
    country = models.CharField(_("country"), max_length=255)

    class Meta:
        verbose_name = _("location")
        verbose_name_plural = _("locations")
        ordering = ["name"]

    def __str__(self):
        return self.name


class Event(ClubScopedModel):
    class EventKind(models.TextChoices):
        TRAINING = "training", _("training")
        MATCH = "match", _("match")
        TOURNAMENT = "tournament", _("tournament")
        MEETING = "meeting", _("meeting")
        SOCIAL = "social", _("social")
        OTHER = "other", _("other")

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
    deadline = models.DateTimeField(_("deadline"), blank=True, null=True)

    location = models.ForeignKey(Location, on_delete=models.SET_NULL, related_name="events", null=True, blank=True, verbose_name=_("location"))
    opponent = models.ForeignKey(Opponent, on_delete=models.SET_NULL, related_name="events", null=True, blank=True, verbose_name=_("opponent"))
    created_by = models.ForeignKey(Member, on_delete=models.SET_NULL, related_name="created_events", null=True, blank=True, verbose_name=_("created by"))

    class Meta:
        verbose_name = _("event")
        verbose_name_plural = _("events")
        ordering = ["-start"]

    def __str__(self):
        return self.title

    def clean(self):
        validate_club_scope(self, self.club_id, same_club_fields=("season", "location", "opponent"))


class EventSeries(ClubScopedModel):
    """A recurring event definition that materialises concrete Event rows."""

    rrule = models.CharField(_("recurrence rule"), max_length=255, help_text=_("RFC 5545 RRULE, e.g. FREQ=WEEKLY;BYDAY=MO,WE."))
    dtstart = models.DateTimeField(_("first occurrence"))
    until = models.DateTimeField(_("until"), null=True, blank=True, help_text=_("Series end: no occurrences are generated after this. Leave blank for open-ended (bounded by the rule's own COUNT/UNTIL, if any)."))
    duration = models.DurationField(_("duration"), null=True, blank=True, help_text=_("Length of each occurrence; sets each event's end."))
    gathering_offset = models.DurationField(_("gathering offset"), null=True, blank=True, help_text=_("How long before the start each occurrence's gathering time is."))
    deadline_offset = models.DurationField(_("deadline offset"), null=True, blank=True, help_text=_("How long before the start each occurrence's sign-up deadline is."))
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
        PRESENT = "present", _("present")
        ABSENT = "absent", _("absent")
        EXCUSED = "excused", _("excused")
        SELECTED = "selected", _("selected")
        NOT_SELECTED = "not_selected", _("not selected")
        MAYBE = "maybe", _("maybe")
        NO_RESPONSE = "no_response", _("no response")

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="attendances", verbose_name=_("event"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="attendances", verbose_name=_("member"))
    status = models.CharField(_("status"), max_length=20, choices=AttendanceStatus.choices, default=AttendanceStatus.NO_RESPONSE)
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
