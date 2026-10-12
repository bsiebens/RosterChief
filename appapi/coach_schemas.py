"""Request and response shapes of the coach endpoints (API.md §7.3)."""

import datetime
import uuid
from typing import Literal

from ninja import Field, Schema

from .schemas import EventOut, TeamRefOut

# --- today --------------------------------------------------------------------------------


class NeedsYouOut(Schema):
    type: Literal["silent_players", "unpublished_lineup"] = Field(description="Ignore types you do not know.")
    event_id: uuid.UUID = Field(description="The event to open: its attendance sheet for `silent_players`, its line-up for `unpublished_lineup`.")
    title: str
    start: datetime.datetime
    count: int | None = Field(description="`silent_players`: how many players have not answered. `unpublished_lineup`: null.")


class TodayOut(Schema):
    team: TeamRefOut
    squad_count: int = Field(description="Players on the team's roster this season.")
    event: EventOut | None = Field(description="The session to show: today's if there is one still current, otherwise the next one. Null when the team has nothing coming up.")
    is_today: bool = Field(description="Whether `event` starts today.")
    closes_at: datetime.datetime | None = Field(description="`event`'s reply deadline (the same as `event.deadline`), null when it has none.")
    in_count: int = Field(description="Players who are in for `event`: `present`, `selected` or `maybe`.")
    out_count: int = Field(description="Players who are out: `absent`, `excused` or `not_selected`.")
    silent_count: int = Field(description="Players who have not answered (`no_response`).")
    needs_you: list[NeedsYouOut] = Field(description="Silent players for `event`, then every upcoming game or tournament (the next five) whose line-up is not published.")


# --- attendance ---------------------------------------------------------------------------


class AttendanceCountsOut(Schema):
    total: int
    responded: int = Field(description="`present`, `selected` or `maybe` -- the default list.")
    silent: int
    declined: int = Field(description="`absent`, `excused` or `not_selected`.")
    checked_in: int = Field(description="Rows with a recorded check-in (`showed_up` not null).")


class AttendanceRowOut(Schema):
    attendance_id: uuid.UUID = Field(description="What a check-in refers to.")
    member_id: uuid.UUID
    first_name: str
    last_name: str
    jersey_number: int | None
    position: str = Field(description="The player's position on this team, empty when not set.")
    status: str = Field(description="`no_response`, `present`, `absent`, `maybe`, `selected`, `not_selected`, `excused`.")
    note: str = Field(description="The reason the player gave with their answer.")
    showed_up: bool | None = Field(description="The recorded check-in; null until one is recorded.")
    photo_url: str | None = Field(description="Same rule as the member photos: null when there is no photo or the account may not see it. Fetch it with the bearer token.")


class AttendanceSheetOut(Schema):
    event: EventOut
    filter: str = Field(description="The filter applied: `responded`, `silent`, `declined` or `all`.")
    counts: AttendanceCountsOut = Field(description="Over all rows, whatever the filter.")
    items: list[AttendanceRowOut] = Field(description="Ordered by last name, first name.")


class CheckInItemIn(Schema):
    attendance_id: uuid.UUID
    showed_up: bool | None = Field(description="`true`/`false` records the check-in, `null` clears it.")


class CheckInsIn(Schema):
    items: list[CheckInItemIn] = Field(max_length=200, description="Any subset of the event's rows. Sending the same item twice is harmless.")


class RemindSilentOut(Schema):
    notified: int = Field(description="Players who got the reminder; 0 when everybody has answered.")


# --- events and series --------------------------------------------------------------------

COACH_KINDS = Literal["training", "game", "tournament", "meeting"]


class LocationRefOut(Schema):
    id: uuid.UUID
    name: str
    address: str
    city: str
    zip_code: str
    country: str = Field(description="ISO 3166-1 alpha-2 code, e.g. `BE`.")
    is_home: bool = Field(description="The club's own ground. Set from the control panel, never through the app.")


class OpponentRefOut(Schema):
    id: uuid.UUID
    name: str


class LocationListOut(Schema):
    items: list[LocationRefOut]


class OpponentListOut(Schema):
    items: list[OpponentRefOut]


class LocationIn(Schema):
    name: str = Field(min_length=1, max_length=255)
    address: str = Field(min_length=1, max_length=255)
    city: str = Field(min_length=1, max_length=255)
    zip_code: str = Field(min_length=1, max_length=255)
    country: str = Field(default="BE", min_length=2, max_length=2, description="ISO 3166-1 alpha-2 code.")


class OpponentIn(Schema):
    name: str = Field(min_length=1, max_length=255)


class SeriesRefOut(Schema):
    id: uuid.UUID
    rrule: str = Field(description="The recurrence rule (RFC 5545).")
    frequency: Literal["weekly", "monthly"] | None = Field(description="Null when the rule is one the app cannot edit (hand-written or imported): show `rrule` and do not offer to change the pattern.")
    interval: int | None
    weekdays: list[str] = Field(description="`MO` … `SU`; only for `weekly`.")
    detached: bool = Field(description="This occurrence is edited on its own: series-wide changes skip it.")


class CoachEventOut(EventOut):
    """What the coach edit screen needs on top of the member shape."""

    series: SeriesRefOut | None = Field(description="Null for a one-off event.")
    location_id: uuid.UUID | None
    opponent_id: uuid.UUID | None
    is_friendly: bool
    external_game_id: str
    max_referees: int
    max_officials: int | None = Field(description="Null when the club does not use the officials module.")
    invited_member_ids: list[uuid.UUID] = Field(description="People invited on top of the team's roster.")
    excluded_member_ids: list[uuid.UUID] = Field(description="Roster players kept out of this event.")


class EventListOut(Schema):
    items: list[CoachEventOut]
    next_cursor: str | None


class CoachEventIn(Schema):
    kind: COACH_KINDS
    title: str = Field(default="", max_length=255, description="Empty: a game gets its match-up (`U16 vs Ajax`) and every other kind its own name. Typing a title for a game is ignored once it has an opponent.")
    start: datetime.datetime
    end: datetime.datetime | None = None
    gathering: datetime.datetime | None = Field(default=None, description="When to be there, if earlier than the start.")
    deadline: datetime.datetime | None = Field(default=None, description="Reply deadline.")
    location_id: uuid.UUID | None = None
    opponent_id: uuid.UUID | None = None
    competition: str = Field(default="", max_length=255, description="A competition the club has switched on, by name; anything else is a validation error.")
    is_friendly: bool = False
    max_referees: int | None = Field(default=None, ge=1)
    max_officials: int | None = Field(default=None, ge=1, description="Ignored unless the club uses the officials module.")
    invited_member_ids: list[uuid.UUID] = Field(default_factory=list, description="People who are not on the roster but are invited.")
    excluded_member_ids: list[uuid.UUID] = Field(default_factory=list, description="Roster players who are not invited.")
    notify: bool = Field(default=True, description="Tell everybody invited, like the web app does: `New game: Tue 14 Oct, 18:30. Let us know if you can make it.`")


class CoachEventPatch(Schema):
    """Only what is sent changes; `null` clears an optional field (`end`, `gathering`, `deadline`, `location_id`, `opponent_id`)."""

    kind: COACH_KINDS | None = None
    title: str | None = Field(default=None, max_length=255)
    start: datetime.datetime | None = None
    end: datetime.datetime | None = None
    gathering: datetime.datetime | None = None
    deadline: datetime.datetime | None = None
    location_id: uuid.UUID | None = None
    opponent_id: uuid.UUID | None = None
    competition: str | None = Field(default=None, max_length=255)
    is_friendly: bool | None = None
    max_referees: int | None = Field(default=None, ge=1)
    max_officials: int | None = Field(default=None, ge=1)
    invited_member_ids: list[uuid.UUID] | None = None
    excluded_member_ids: list[uuid.UUID] | None = None
    score_for: int | None = Field(default=None, ge=0)
    score_against: int | None = Field(default=None, ge=0)
    is_live: bool | None = None
    scope: Literal["this", "this_and_following", "all"] = Field(
        default="this",
        description=(
            "For an occurrence of a series. `this`: only this event (and it stops following the series, if the change touches a field the series sets). "
            "`this_and_following`: this and every later occurrence (the series is split in two). `all`: every upcoming occurrence that still follows the series. "
            "Fields the series does not set (`start`, `competition`, `is_friendly`, `max_*`, scores, `is_live`) always change this event only. "
            "`this_and_following` and `all` on a one-off event: `422 not_in_series`."
        ),
    )
    notify: bool = Field(default=False, description="Tell everybody invited that the event changed.")


class EventUpdateOut(Schema):
    event: CoachEventOut
    occurrences_updated: int = Field(description="Other occurrences of the series changed along with this one; 0 for scope `this`.")


class SeriesOut(Schema):
    id: uuid.UUID
    title: str
    kind: str
    rrule: str
    frequency: Literal["weekly", "monthly"] | None
    interval: int | None
    weekdays: list[str]
    dtstart: datetime.datetime = Field(description="First occurrence; its time of day is every occurrence's time.")
    until: datetime.datetime | None
    duration_minutes: int | None
    gathering_minutes_before: int | None
    deadline_minutes_before: int | None
    location_id: uuid.UUID | None
    opponent_id: uuid.UUID | None
    invited_member_ids: list[uuid.UUID]
    excluded_member_ids: list[uuid.UUID]
    occurrences_created: int = Field(description="Events generated by this call (create, and edit when the pattern now reaches further); 0 on a plain read.")
    occurrences_updated: int = Field(description="Upcoming occurrences that took the new template; 0 on create and read.")


class SeriesIn(Schema):
    title: str = Field(min_length=1, max_length=255)
    kind: COACH_KINDS
    dtstart: datetime.datetime = Field(description="First occurrence. Its time of day is used for every occurrence.")
    until: datetime.datetime | None = Field(default=None, description="Last moment an occurrence may start; null for open-ended (generated 90 days ahead and extended nightly).")
    frequency: Literal["weekly", "monthly"]
    interval: int = Field(default=1, ge=1, le=52)
    weekdays: list[Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]] = Field(default_factory=list, description="Required (at least one) for `weekly`; ignored for `monthly`, which repeats on `dtstart`'s day of the month.")
    duration_minutes: int | None = Field(default=None, ge=0)
    gathering_minutes_before: int | None = Field(default=None, ge=0)
    deadline_minutes_before: int | None = Field(default=None, ge=0)
    location_id: uuid.UUID | None = None
    opponent_id: uuid.UUID | None = None
    invited_member_ids: list[uuid.UUID] = Field(default_factory=list)
    excluded_member_ids: list[uuid.UUID] = Field(default_factory=list)


class SeriesPatch(Schema):
    """Only what is sent changes. The template reaches every upcoming occurrence that still follows the series; occurrences that no longer fit a changed pattern stay (delete them one by one)."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    kind: COACH_KINDS | None = None
    dtstart: datetime.datetime | None = None
    until: datetime.datetime | None = None
    frequency: Literal["weekly", "monthly"] | None = None
    interval: int | None = Field(default=None, ge=1, le=52)
    weekdays: list[Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]] | None = None
    duration_minutes: int | None = Field(default=None, ge=0)
    gathering_minutes_before: int | None = Field(default=None, ge=0)
    deadline_minutes_before: int | None = Field(default=None, ge=0)
    location_id: uuid.UUID | None = None
    opponent_id: uuid.UUID | None = None
    invited_member_ids: list[uuid.UUID] | None = None
    excluded_member_ids: list[uuid.UUID] | None = None


# --- lineup -------------------------------------------------------------------------------


class LineupMemberOut(Schema):
    member_id: uuid.UUID
    first_name: str
    last_name: str
    jersey_number: int | None
    position: str = Field(description="The player's position on this team, empty when not set (a guest call-up).")
    position_order: int = Field(description="Sort key of the position (low first), so the app can group like the web does.")
    rsvp_status: str = Field(description="`present`, `maybe`, `selected`, `not_selected` (available), or `absent`, `excused`, `no_response` (not available).")
    note: str = Field(description="The reason the player gave with their answer.")
    available: bool = Field(description="False for `absent`, `excused` and `no_response`: they cannot be picked.")
    selected: bool = Field(description="In the line-up as it is saved now (published or not).")
    can_drop_out: bool = Field(description="Selected in a published line-up and the event has not started: the player can still pull out themselves.")
    attendance_rate: int | None = Field(description="This season's turnout in percent (present of present + absent over past events); null before any reply.")
    photo_url: str | None


class LineupChangesOut(Schema):
    selected_member_ids: list[uuid.UUID] = Field(description='Would be told "you\'re in".')
    dropped_member_ids: list[uuid.UUID] = Field(description='Would be told "you\'re not in it this time" (they were in the last published line-up).')


class LineupOut(Schema):
    event: EventOut
    status: Literal["none", "draft", "scheduled", "published"] = Field(
        description="`none`: nobody has opened or saved one yet. `draft`: saved picks, never published. `scheduled`: publishes by itself at `scheduled_publish_at`. `published`: players have been told (edits since then are `pending`)."
    )
    published_at: datetime.datetime | None
    scheduled_publish_at: datetime.datetime | None
    selected_count: int
    pending: LineupChangesOut = Field(description="Who publishing now would notify. Empty when the saved picks equal what players were last told.")
    members: list[LineupMemberOut] = Field(description="Everybody invited, ordered by position then name.")


class LineupIn(Schema):
    selected_member_ids: list[uuid.UUID] = Field(max_length=200, description="The complete set of selected players. Anybody not listed is deselected.")


class LineupPublishIn(Schema):
    mode: Literal["now", "schedule", "cancel"] = Field(description="`now`: publish and notify. `schedule`: publish by itself at `at`. `cancel`: drop a scheduled publish.")
    at: datetime.datetime | None = Field(default=None, description="Required for `schedule`, in the future.")


class LineupPublishOut(Schema):
    lineup: LineupOut
    notified: LineupChangesOut = Field(description="Who was told by this call; empty for `schedule` and `cancel`.")


# --- roster -------------------------------------------------------------------------------


class PositionOut(Schema):
    id: uuid.UUID
    name: str
    short_name: str
    ordering: int
    management_position: bool = Field(description="Staff positions only: the holder manages the team.")


class RosterPlayerOut(Schema):
    membership_id: uuid.UUID = Field(description="What `PATCH` and `DELETE …/roster/{membership_id}` take.")
    member_id: uuid.UUID
    first_name: str
    last_name: str
    position_id: uuid.UUID | None
    position: str = Field(description="Empty until a position is set.")
    jersey_number: int | None
    is_captain: bool
    is_alternate_captain: bool
    sign_up_pending: bool = Field(description="The club has not approved this player's sign-up yet.")
    photo_url: str | None


class RosterStaffOut(Schema):
    assignment_id: uuid.UUID = Field(description="What `DELETE …/staff/{assignment_id}` takes.")
    member_id: uuid.UUID
    first_name: str
    last_name: str
    position_id: uuid.UUID
    position: str
    is_manager: bool = Field(description="Holds a management position on the team.")
    is_me: bool = Field(description="The caller's own assignment: it cannot be removed here (`403 self_removal`).")
    photo_url: str | None


class RosterOut(Schema):
    season_id: uuid.UUID | None = Field(description="The current season; the lists are empty without one.")
    players: list[RosterPlayerOut] = Field(description="Ordered by position, then last name.")
    staff: list[RosterStaffOut] = Field(description="Ordered by position, then last name.")
    player_positions: list[PositionOut] = Field(description="What a player's `position_id` may be.")
    staff_positions: list[PositionOut] = Field(description="What a staff member's `position_id` may be.")


class CandidateOut(Schema):
    member_id: uuid.UUID
    first_name: str
    last_name: str
    photo_url: str | None


class CandidateListOut(Schema):
    items: list[CandidateOut]
    next_cursor: str | None


class AddPlayersIn(Schema):
    member_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class AddStaffIn(Schema):
    member_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    position_id: uuid.UUID = Field(description="One staff position for all of them.")


class RosterPatch(Schema):
    """Only what is sent changes. `jersey_number: null` clears it."""

    position_id: uuid.UUID | None = None
    jersey_number: int | None = Field(default=None, ge=0, le=32767)
    is_captain: bool | None = None
    is_alternate_captain: bool | None = None


# --- news ---------------------------------------------------------------------------------


class NewsPhotoOut(Schema):
    id: uuid.UUID
    url: str = Field(description="Absolute URL of the image.")
    is_main: bool = Field(description="The cover photo (the first one submitted).")


class CoachNewsOut(Schema):
    id: uuid.UUID
    title: str
    body: str = Field(description="Markdown, as written.")
    status: Literal["draft", "pending_review", "published"] = Field(
        description="`pending_review`: waiting for an editor. `published`: live (check `published_at`; a future date means scheduled). "
        "`draft`: an editor took it back out of the review queue, or unpublished it; the app cannot resubmit it, ask the editor."
    )
    published_at: datetime.datetime | None
    created: datetime.datetime
    teams: list[TeamRefOut]
    photos: list[NewsPhotoOut]


class CoachNewsListOut(Schema):
    items: list[CoachNewsOut]
    next_cursor: str | None


# --- evaluations --------------------------------------------------------------------------

from .schemas import AnswerValueOut, FormFieldOut  # noqa: E402


class ChecklistOut(Schema):
    slug: str = Field(description="What `POST …/evaluations` takes as `checklist_slug`.")
    name: str
    description: str
    fields: list[FormFieldOut] = Field(
        description="The questions in order, to render exactly like a form. A rating scale is a `choice` field whose `options` are the steps (for example 1 to 5); there is no separate scale type. Ignore a field type you do not know."
    )


class ChecklistListOut(Schema):
    items: list[ChecklistOut]
    file_accept: str = Field(description="Accepted file types for `file` fields.")
    max_file_bytes: int


class EvaluationRefOut(Schema):
    slug: str
    name: str


class EvaluatorOut(Schema):
    first_name: str
    last_name: str


class EvaluationSummaryOut(Schema):
    id: uuid.UUID
    checklist: EvaluationRefOut
    season_id: uuid.UUID
    evaluator: EvaluatorOut | None = Field(description="Null when the evaluator has left the club.")
    created: datetime.datetime


class EvaluationDetailOut(EvaluationSummaryOut):
    answers: list[AnswerValueOut] = Field(description="Every question of the version the evaluation was scored against, in order; `value` is null where left blank.")


class EvaluationListOut(Schema):
    items: list[EvaluationSummaryOut]
    next_cursor: str | None
