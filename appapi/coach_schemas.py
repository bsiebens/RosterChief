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
