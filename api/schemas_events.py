"""Schemas for the calendar API (``/api/v1/events``) and the attendance statistics on teams, members and the overview."""

import datetime
import uuid

from ninja import Schema

# -- references --


class Ref(Schema):
    id: uuid.UUID
    name: str
    short_name: str = ""  # teams only: what a generated game title uses


class LocationRef(Schema):
    id: uuid.UUID
    name: str
    city: str = ""
    address: str = ""
    is_home: bool = False


class OpponentRef(Schema):
    id: uuid.UUID
    name: str
    logo_url: str | None = None


class EventRef(Schema):
    id: uuid.UUID
    title: str
    kind: str
    start: datetime.datetime


class Choice(Schema):
    value: str
    label: str


class EventSubtypeRef(Schema):
    id: uuid.UUID
    kind: str
    name: str
    title_template: str = ""


# -- calendar --


class AttendanceCounts(Schema):
    present: int = 0
    absent: int = 0
    excused: int = 0
    maybe: int = 0
    no_response: int = 0
    selected: int = 0
    not_selected: int = 0
    total: int = 0  # invited (attendance rows)


class EventRowOut(Schema):
    id: uuid.UUID
    title: str
    kind: str
    subtype_id: uuid.UUID | None = None
    subtype_name: str = ""
    start: datetime.datetime
    end: datetime.datetime | None = None
    gathering: datetime.datetime | None = None
    deadline: datetime.datetime | None = None
    cancelled: bool
    location: LocationRef | None = None
    opponent: OpponentRef | None = None
    teams: list[Ref]
    groups: list[Ref]
    club_wide: bool
    series_id: uuid.UUID | None = None
    detached: bool
    is_friendly: bool
    score_for: int | None = None
    score_against: int | None = None
    is_home_game: bool = False
    has_game_source: bool = False  # a competition game id to fetch the score and game sheet from
    attendance: AttendanceCounts
    can_edit: bool
    can_manage_attendance: bool


class RecurrenceValues(Schema):
    frequency: str | None = None
    interval: int | None = None
    weekdays: list[str] = []
    count: int | None = None
    until: datetime.date | None = None
    rrule: str = ""
    friendly: bool = True


class SeriesInfo(Schema):
    id: uuid.UUID
    title: str
    rrule: str
    description: str
    dtstart: datetime.datetime
    until: datetime.datetime | None = None
    recurrence: RecurrenceValues


class LineupInfo(Schema):
    available: bool  # games and tournaments only
    status: str  # none / draft / scheduled / published
    team_id: uuid.UUID | None = None
    published_at: datetime.datetime | None = None
    scheduled_publish_at: datetime.datetime | None = None


class OfficiatingRef(Schema):
    """A referee or official on the game (no fees: those are for the referee management screen)."""

    name: str
    member_id: uuid.UUID | None = None
    type_name: str = ""
    is_external: bool = False


class EventDetailOut(EventRowOut):
    season: Ref | None = None
    invited_member_ids: list[uuid.UUID]
    excluded_member_ids: list[uuid.UUID]
    invited_members: list[Ref]
    excluded_members: list[Ref]
    series: SeriesInfo | None = None
    lineup: LineupInfo
    competition: str = ""
    competition_label: str = ""
    is_live: bool = False
    created_by: Ref | None = None
    task_count: int = 0
    attachment_count: int = 0
    has_game_sheet: bool = False
    referees: list[OfficiatingRef] = []
    officials: list[OfficiatingRef] = []


class WeekdayChoice(Schema):
    code: str
    label: str


class EventOptionsOut(Schema):
    """What the add / edit form may offer the caller (never something ``can_schedule`` would refuse)."""

    can_create: bool
    teams: list[Ref]
    groups: list[Ref]
    filter_teams: list[Ref] = []  # every team and group, for the calendar's filters (teams/groups above are what the user may schedule for)
    filter_groups: list[Ref] = []
    can_club_wide: bool
    locations: list[LocationRef]
    opponents: list[OpponentRef]
    kinds: list[Choice]
    subtypes: list[EventSubtypeRef]
    frequencies: list[str]
    weekdays: list[WeekdayChoice]


class RecurrenceIn(Schema):
    frequency: str
    interval: int = 1
    weekdays: list[str] = []
    until: datetime.date | None = None
    count: int | None = None


class EventIn(Schema):
    """A one-off event, or (with ``recurrence`` or ``rrule``) a recurring series. ``season_id`` and ``is_friendly`` are
    for one-off events only. ``notify`` (default true) tells the invited people with a login."""

    title: str = ""
    kind: str = "other"
    subtype_id: uuid.UUID | None = None
    start: datetime.datetime
    end: datetime.datetime | None = None
    gathering: datetime.datetime | None = None
    deadline: datetime.datetime | None = None
    location_id: uuid.UUID | None = None
    opponent_id: uuid.UUID | None = None
    team_ids: list[uuid.UUID] = []
    group_ids: list[uuid.UUID] = []
    club_wide: bool = False
    invited_member_ids: list[uuid.UUID] = []
    excluded_member_ids: list[uuid.UUID] = []
    season_id: uuid.UUID | None = None
    is_friendly: bool = False
    recurrence: RecurrenceIn | None = None
    rrule: str | None = None
    notify: bool = True


class EventPatch(Schema):
    """Only sent fields change. ``recurrence`` / ``rrule`` (scope ``all`` on an occurrence) changes the series' rule."""

    title: str | None = None
    kind: str | None = None
    subtype_id: uuid.UUID | None = None
    start: datetime.datetime | None = None
    end: datetime.datetime | None = None
    gathering: datetime.datetime | None = None
    deadline: datetime.datetime | None = None
    location_id: uuid.UUID | None = None
    opponent_id: uuid.UUID | None = None
    team_ids: list[uuid.UUID] | None = None
    group_ids: list[uuid.UUID] | None = None
    club_wide: bool | None = None
    invited_member_ids: list[uuid.UUID] | None = None
    excluded_member_ids: list[uuid.UUID] | None = None
    season_id: uuid.UUID | None = None
    is_friendly: bool | None = None
    cancelled: bool | None = None
    recurrence: RecurrenceIn | None = None
    rrule: str | None = None
    notify: bool = True


class EventValues(Schema):
    title: str
    kind: str
    subtype_id: uuid.UUID | None = None
    start: datetime.datetime | None = None
    end: datetime.datetime | None = None
    gathering: datetime.datetime | None = None
    deadline: datetime.datetime | None = None
    location_id: uuid.UUID | None = None
    opponent_id: uuid.UUID | None = None
    team_ids: list[uuid.UUID]
    group_ids: list[uuid.UUID]
    club_wide: bool
    invited_member_ids: list[uuid.UUID]
    excluded_member_ids: list[uuid.UUID]
    is_friendly: bool = False
    cancelled: bool = False
    recurrence: RecurrenceValues | None = None


class EventFormOut(Schema):
    """The edit form's starting values: ``event`` for "this occurrence" / a one-off, ``series`` for the whole series."""

    event: EventValues
    series: EventValues | None = None
    series_description: str = ""
    can_edit: bool


# -- attendance --


class AttendanceRowOut(Schema):
    member: Ref
    first_name: str
    last_name: str
    status: str
    showed_up: bool | None = None
    note: str = ""
    coach_note: str | None = None  # only for people who manage this event's attendance
    answered_by_coach: bool = False
    answered_by_name: str | None = None
    answered_via: str | None = None
    coach_set_at: datetime.datetime | None = None
    position: str = ""
    jersey_number: int | None = None
    team_id: uuid.UUID | None = None
    selected: bool = False
    has_user: bool = False


class AttendanceChangeOut(Schema):
    id: uuid.UUID
    created: datetime.datetime
    from_status: str
    to_status: str
    changed_by_name: str
    via: str
    note_changed: bool = False


class BlockedMemberOut(Schema):
    member_id: uuid.UUID
    first_name: str
    last_name: str
    #: The open requirements that keep this person out of the event.
    requirements: list[str]


class AttendanceListOut(Schema):
    can_manage: bool
    counts: AttendanceCounts
    checked_in: int
    no_shows: int
    rows: list[AttendanceRowOut]
    #: People who would be invited but cannot join because of open requirements (only for those who manage the attendance; empty otherwise).
    blocked: list[BlockedMemberOut] = []


class AnswerIn(Schema):
    status: str | None = None  # None: only the coach note changes
    coach_note: str | None = None
    notify: bool = False


class CheckInIn(Schema):
    showed_up: bool | None = None


class RemindOut(Schema):
    reminded: int


class BulkAnswerIn(Schema):
    member_ids: list[uuid.UUID]
    status: str
    notify: bool = False


class BulkResult(Schema):
    member_id: uuid.UUID
    ok: bool
    status: str | None = None
    error: str = ""


class BulkAnswerOut(Schema):
    results: list[BulkResult]


# -- line-up --


class LineupMemberOut(Schema):
    member: Ref
    position: str
    jersey_number: int | None = None
    status: str
    available: bool
    on_roster: bool
    selected: bool
    slots: list[str] = []  # "<group key>.<slot key>" of the sport's line-up template, in template order; [] = not in a line (a player may hold several)
    # Only for attendance managers: the player's own reason for this event and their season turnout (None: too little data).
    note: str = ""
    games_rate: int | None = None
    practices_rate: int | None = None


class LineupOut(Schema):
    available: bool
    can_manage: bool
    status: str
    team: Ref | None = None
    teams: list[Ref]
    published_at: datetime.datetime | None = None
    scheduled_publish_at: datetime.datetime | None = None
    members: list[LineupMemberOut]
    slots_enabled: bool = False  # set per sport by platform admins; slots are informational only (never used for statistics)
    template: list[dict] = []  # [{key, label, slots: [{key, label}]}]


class LineupSlotIn(Schema):
    slot: str
    member_id: uuid.UUID | None = None  # None / omitted = the slot stays empty


class LineupIn(Schema):
    team_id: uuid.UUID | None = None
    member_ids: list[uuid.UUID]
    # slot key -> member id (a slot holds one player, a player may hold several slots), or a list of {slot, member_id}; omitted = leave the slots as they are
    slots: dict[str, uuid.UUID | None] | list[LineupSlotIn] | None = None


class LineupScheduleIn(Schema):
    when: datetime.datetime


class LineupPublishOut(Schema):
    lineup: LineupOut
    newly_selected: list[uuid.UUID]
    dropped: list[uuid.UUID]


# -- tasks --


class ClaimOut(Schema):
    id: uuid.UUID
    label: str  # "<Family> family" or the member's name: what everyone sees
    member_id: uuid.UUID | None = None  # task managers only
    member_name: str | None = None  # task managers only


class TaskOut(Schema):
    id: uuid.UUID
    title: str
    description: str
    needed_quantity: int
    claimed_count: int
    open_slots: int
    claimed_by_me: bool
    claims: list[ClaimOut]
    created_by: Ref | None = None


class TaskListOut(Schema):
    can_manage: bool
    tasks: list[TaskOut]


class TaskIn(Schema):
    title: str
    description: str = ""
    needed_quantity: int = 1
    notify: bool = False


class TaskPatch(Schema):
    title: str | None = None
    description: str | None = None
    needed_quantity: int | None = None


class ClaimIn(Schema):
    member_id: uuid.UUID | None = None


# -- attachments --


class AttachmentOut(Schema):
    id: uuid.UUID
    name: str
    size: int
    uploaded_by: Ref | None = None
    created: datetime.datetime
    download_url: str


class AttachmentListOut(Schema):
    can_manage: bool
    attachments: list[AttachmentOut]


# -- statistics --


class SeasonRef(Schema):
    id: uuid.UUID
    name: str


class PersonRate(Schema):
    id: uuid.UUID
    name: str
    rate: int
    responses: int


class NoShowOut(Schema):
    member: Ref
    event: EventRef


class TeamAttendanceOut(Schema):
    season: SeasonRef | None = None
    rate: int | None = None
    sessions: int
    top_attenders: list[PersonRate]
    needs_attention: list[PersonRate]
    missed_recent_practices: list[Ref]
    no_shows: list[NoShowOut]
    no_show_count: int


class MemberCounts(Schema):
    present: int
    absent: int
    no_reply: int
    no_shows: int
    excused: int = 0
    maybe: int = 0
    selected: int = 0  # picked for a game (counts as having been there)


class SparkBar(Schema):
    id: uuid.UUID
    title: str
    kind: str
    start: datetime.datetime
    state: str  # present / absent / upcoming


class RecentEvent(Schema):
    event: EventRef
    status: str
    showed_up: bool | None = None
    cancelled: bool = False


class MemberAttendanceOut(Schema):
    season: SeasonRef | None = None
    counts: MemberCounts
    rate: int | None = None
    sparkline: list[SparkBar]
    recent: list[RecentEvent]


class GameSheetRow(Schema):
    time: str = ""
    type: str
    ours: bool | None = None
    team: str = ""
    icon: str = ""
    kind: str = ""
    who: str = ""
    detail: str = ""
    tag: str = ""
    score: str = ""
    serious: bool = False


class GameSheetPeriod(Schema):
    label: str
    title: str
    score: str = ""
    rows: list[GameSheetRow]


class PeriodScore(Schema):
    label: str
    score: str


class LineupPlayerOut(Schema):
    pos: str = ""
    number: int | None = None
    name: str
    member_id: uuid.UUID | None = None  # our side only: the roster member the sheet name matched
    photo_url: str | None = None
    initials: str = ""
    goals: int = 0
    assists: int = 0
    penalty_minutes: int = 0


class GameLineupOut(Schema):
    """Who dressed for each side, as the competition's game page lists them (ours first)."""

    ours_label: str
    theirs_label: str
    ours: list[LineupPlayerOut]
    theirs: list[LineupPlayerOut]


class GameSheetOut(Schema):
    """The game's timeline laid out from the club's own side (ours first), names included."""

    status: str
    final: bool
    updated_at: datetime.datetime | None = None
    source: str
    ours_label: str
    theirs_label: str
    score: str = ""
    period_scores: list[PeriodScore]
    periods: list[GameSheetPeriod]
    lineup: GameLineupOut | None = None


class GameMomentOut(Schema):
    """One detected piece of news of a game, for debugging the live push."""

    id: uuid.UUID
    key: str
    kind: str
    kind_label: str
    description: str
    game_seconds: int | None = None
    detected_at: datetime.datetime
    notifiable: bool  # false: recorded as a catch-up (too old in game time to tell anyone)
    delivered_at: datetime.datetime | None = None
    state: str  # pushed | catch_up | not_pushed


class FollowStateOut(Schema):
    directly: bool
    via_team: bool


class FollowedTeamsOut(Schema):
    team_ids: list[uuid.UUID]
