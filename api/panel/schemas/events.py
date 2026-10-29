"""Request and response shapes of the management events routes (``/manage/events``).

The event and series shapes extend the coach app's (api.protected.coach_schemas) with what only
the management screens have: groups, whole-club events, the season and what the caller may do.
"""

import datetime
import uuid
from typing import Literal

from ninja import Field, Schema

from api.protected.coach_schemas import CoachEventOut, LocationRefOut, OpponentRefOut, SeriesOut
from api.protected.schemas import EventOut

Weekday = Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


class PanelEventOut(CoachEventOut):
    group_ids: list[uuid.UUID] = Field(description="Groups whose members are invited.")
    club_wide: bool = Field(description="Every active club member of the season is invited (admin only).")
    season_id: uuid.UUID | None = Field(description="The season set on the event; null when it is derived from `start`.")
    detached: bool = Field(description="An occurrence edited on its own: series-wide changes skip it.")
    can_manage: bool = Field(description="The caller may edit, cancel and detach it and set attendance.")


class PanelEventDetailOut(PanelEventOut):
    attendance_counts: dict[str, int] = Field(description="Invited people per attendance status (every status is present, zero included).")


class PanelEventListOut(Schema):
    items: list[PanelEventOut]
    next_cursor: str | None


class PanelEventIn(Schema):
    kind: str = Field(description="`training`, `game`, `tournament`, `meeting`, `social` or `other`.")
    title: str = Field(default="", max_length=255, description="Empty: a game gets its match-up, a new training or tournament its own name. Every other kind needs one.")
    team_ids: list[uuid.UUID] = Field(default_factory=list, description="Teams invited. A game needs at least one; a non-admin needs a team or a group.")
    group_ids: list[uuid.UUID] = Field(default_factory=list)
    club_wide: bool = Field(default=False, description="Admin only; cannot be combined with teams or groups.")
    start: datetime.datetime
    end: datetime.datetime | None = None
    gathering: datetime.datetime | None = None
    deadline: datetime.datetime | None = Field(default=None, description="Reply deadline.")
    location_id: uuid.UUID | None = None
    opponent_id: uuid.UUID | None = None
    competition: str = Field(default="", max_length=255, description="A competition the club has switched on, by name.")
    is_friendly: bool = False
    external_game_id: str = Field(default="", max_length=255)
    max_referees: int | None = Field(default=None, ge=1)
    max_officials: int | None = Field(default=None, ge=1, description="Ignored unless the club uses the officials module.")
    invited_member_ids: list[uuid.UUID] = Field(default_factory=list)
    excluded_member_ids: list[uuid.UUID] = Field(default_factory=list)
    notify: bool = Field(default=True, description="Tell everybody invited, like the web screen does.")


class PanelEventPatch(Schema):
    """Only what is sent changes; `null` clears an optional field. Scores live in `PUT …/result`."""

    kind: str | None = None
    title: str | None = Field(default=None, max_length=255)
    team_ids: list[uuid.UUID] | None = None
    group_ids: list[uuid.UUID] | None = None
    club_wide: bool | None = None
    start: datetime.datetime | None = None
    end: datetime.datetime | None = None
    gathering: datetime.datetime | None = None
    deadline: datetime.datetime | None = None
    location_id: uuid.UUID | None = None
    opponent_id: uuid.UUID | None = None
    competition: str | None = Field(default=None, max_length=255)
    is_friendly: bool | None = None
    external_game_id: str | None = Field(default=None, max_length=255)
    max_referees: int | None = Field(default=None, ge=1)
    max_officials: int | None = Field(default=None, ge=1)
    invited_member_ids: list[uuid.UUID] | None = None
    excluded_member_ids: list[uuid.UUID] | None = None
    scope: Literal["this", "this_and_following", "all"] = Field(
        default="this",
        description=(
            "For an occurrence of a series. `this`: only this event (it stops following the series if the change touches a field the series sets). "
            "`this_and_following`: this and every later occurrence (the series is split in two). `all`: every upcoming occurrence that still follows the series. "
            "Fields the series does not set (`start`, `teams`, `groups`, `competition`, ...) always change this event only. `422 not_in_series` on a one-off event."
        ),
    )
    notify: bool = Field(default=False, description="Tell everybody invited that the event changed.")


class PanelEventUpdateOut(Schema):
    event: PanelEventOut
    occurrences_updated: int = Field(description="Other occurrences changed along with this one; 0 for scope `this`.")


class PanelResultIn(Schema):
    score_for: int | None = Field(default=None, ge=0, description="Our goals/points. Send both scores or neither (null clears the result).")
    score_against: int | None = Field(default=None, ge=0)
    is_live: bool = Field(default=False, description="The game is in progress.")


# --- series -------------------------------------------------------------------------------


class PanelSeriesOut(SeriesOut):
    team_ids: list[uuid.UUID]
    group_ids: list[uuid.UUID]
    club_wide: bool
    can_manage: bool


class PanelSeriesListOut(Schema):
    items: list[PanelSeriesOut]
    next_cursor: str | None


class PanelSeriesIn(Schema):
    title: str = Field(min_length=1, max_length=255)
    kind: str
    team_ids: list[uuid.UUID] = Field(default_factory=list)
    group_ids: list[uuid.UUID] = Field(default_factory=list)
    club_wide: bool = False
    dtstart: datetime.datetime = Field(description="First occurrence. Its time of day is used for every occurrence.")
    until: datetime.datetime | None = Field(default=None, description="Last moment an occurrence may start; null for open-ended (generated 90 days ahead and extended nightly).")
    frequency: Literal["weekly", "monthly"] = "weekly"
    interval: int = Field(default=1, ge=1, le=52)
    weekdays: list[Weekday] = Field(default_factory=list, description="At least one for `weekly`; ignored for `monthly`.")
    advanced_rrule: str = Field(default="", description="A raw RFC 5545 RRULE; when set it overrides `frequency`, `interval` and `weekdays`.")
    duration_minutes: int | None = Field(default=None, ge=0)
    gathering_minutes_before: int | None = Field(default=None, ge=0)
    deadline_minutes_before: int | None = Field(default=None, ge=0)
    location_id: uuid.UUID | None = None
    opponent_id: uuid.UUID | None = None
    invited_member_ids: list[uuid.UUID] = Field(default_factory=list)
    excluded_member_ids: list[uuid.UUID] = Field(default_factory=list)


class PanelSeriesPatch(Schema):
    """Only what is sent changes. The template reaches every upcoming occurrence that still follows the series; a new pattern only decides future dates."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    kind: str | None = None
    team_ids: list[uuid.UUID] | None = None
    group_ids: list[uuid.UUID] | None = None
    club_wide: bool | None = None
    dtstart: datetime.datetime | None = None
    until: datetime.datetime | None = None
    frequency: Literal["weekly", "monthly"] | None = None
    interval: int | None = Field(default=None, ge=1, le=52)
    weekdays: list[Weekday] | None = None
    advanced_rrule: str | None = None
    duration_minutes: int | None = Field(default=None, ge=0)
    gathering_minutes_before: int | None = Field(default=None, ge=0)
    deadline_minutes_before: int | None = Field(default=None, ge=0)
    location_id: uuid.UUID | None = None
    opponent_id: uuid.UUID | None = None
    invited_member_ids: list[uuid.UUID] | None = None
    excluded_member_ids: list[uuid.UUID] | None = None


class GeneratedOut(Schema):
    occurrences_created: int


# --- attendance ---------------------------------------------------------------------------


class PanelAttendanceRowOut(Schema):
    attendance_id: uuid.UUID
    member_id: uuid.UUID
    first_name: str
    last_name: str
    status: str = Field(description="`no_response`, `present`, `absent`, `excused`, `maybe`, `selected`, `not_selected`.")
    note: str = Field(description="The reason the person gave.")
    showed_up: bool | None
    answered_by_coach: bool
    coach_note: str | None = Field(description="The private coach note; null unless the caller may manage the event.")


class PanelAttendanceOut(Schema):
    event: EventOut
    counts: dict[str, int] = Field(description="Per status (zero included) plus `total`, whatever the filter.")
    items: list[PanelAttendanceRowOut] = Field(description="Ordered by last name, first name.")


# --- pickers ------------------------------------------------------------------------------


class PanelLocationListOut(Schema):
    items: list[LocationRefOut]


class PanelOpponentListOut(Schema):
    items: list[OpponentRefOut]
