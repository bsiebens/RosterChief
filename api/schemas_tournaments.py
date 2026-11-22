"""Request bodies of the tournament endpoints (management, public registration, portal). Responses are plain JSON documents described in
docs/tournaments-api.md. Money is a string with 2 decimals; datetimes are ISO 8601 with offset; dates ``YYYY-MM-DD``; times ``HH:MM``."""

import datetime
import uuid
from decimal import Decimal
from typing import Annotated, Literal

from ninja import Field, Schema

MoneyIn = Annotated[Decimal, Field(ge=0, max_digits=8, decimal_places=2)]
Color = Annotated[str, Field(max_length=7, pattern=r"^(#[0-9A-Fa-f]{6})?$")]
Role = Literal["player", "staff"]
Status = Literal["draft", "published", "in_progress", "completed", "cancelled"]
LunchMode = Literal["none", "optional", "mandatory"]
PaymentStatus = Literal["unpaid", "deposit_received", "fully_paid"]
StageFormat = Literal["single_elim", "double_elim", "round_robin", "groups", "swiss", "custom"]
Tiebreaker = Literal["goal_diff", "goals_for", "goals_against", "won", "head_to_head"]
Trigger = Literal["game_count", "elapsed_time", "round_boundary", "clock_window"]


# --- tournament -----------------------------------------------------------------------------------------------------------


class DayIn(Schema):
    date: datetime.date
    starts_at: datetime.time
    ends_at: datetime.time


class TournamentIn(Schema):
    name: str = Field(max_length=255, min_length=1)
    status: Status = "draft"
    days: list[DayIn] = Field(min_length=1, max_length=60)
    registration_open: bool = True
    max_teams: int | None = Field(None, ge=1, le=32767)
    earliest_birth_year: int | None = Field(None, ge=1900, le=2200)
    latest_birth_year: int | None = Field(None, ge=1900, le=2200)
    contact_email: str = Field("", max_length=254)
    description: str = ""
    home_team_id: uuid.UUID | None = None
    deposit_amount: MoneyIn | None = None
    entry_fee_amount: MoneyIn | None = None
    lunch_mode: LunchMode = "none"
    lunch_price_per_day: MoneyIn | None = None
    lunch_billed_with_fee: bool = True
    default_match_duration_minutes: int = Field(45, ge=1, le=1440)
    default_gap_minutes: int = Field(5, ge=0, le=240)


class TournamentPatch(Schema):
    name: str | None = Field(None, max_length=255, min_length=1)
    status: Status | None = None
    days: list[DayIn] | None = Field(None, min_length=1, max_length=60)
    registration_open: bool | None = None
    max_teams: int | None = Field(None, ge=1, le=32767)
    earliest_birth_year: int | None = Field(None, ge=1900, le=2200)
    latest_birth_year: int | None = Field(None, ge=1900, le=2200)
    contact_email: str | None = Field(None, max_length=254)
    description: str | None = None
    home_team_id: uuid.UUID | None = None
    deposit_amount: MoneyIn | None = None
    entry_fee_amount: MoneyIn | None = None
    lunch_mode: LunchMode | None = None
    lunch_price_per_day: MoneyIn | None = None
    lunch_billed_with_fee: bool | None = None
    default_match_duration_minutes: int | None = Field(None, ge=1, le=1440)
    default_gap_minutes: int | None = Field(None, ge=0, le=240)


class RejectIn(Schema):
    reason: str = ""


class WithdrawIn(Schema):
    reason: str = ""


class PaymentIn(Schema):
    payment_status: PaymentStatus


class ManualTeamIn(Schema):
    """``kind="club"``: one of the club's own teams (``team_id``). ``kind="foreign"``: another club's team (name, contact name and email)."""

    kind: Literal["club", "foreign"]
    team_id: uuid.UUID | None = None
    foreign_name: str = Field("", max_length=255)
    foreign_contact_name: str = Field("", max_length=255)
    foreign_contact_email: str = Field("", max_length=254)
    foreign_contact_phone: str = Field("", max_length=32)
    foreign_primary_color: Color = ""
    foreign_secondary_color: Color = ""


# --- roster ---------------------------------------------------------------------------------------------------------------


class RosterEntryIn(Schema):
    first_name: str = Field("", max_length=150)
    last_name: str = Field("", max_length=150)
    jersey_number: int | None = Field(None, ge=0, le=32767)
    role: Role = "player"
    position_label: str = Field("", max_length=100)
    date_of_birth: datetime.date | None = None
    license_number: str = Field("", max_length=100)
    dietary_restrictions: str = ""
    lunch_days: list[datetime.date] | None = None


class RosterEntryPatch(Schema):
    first_name: str | None = Field(None, max_length=150)
    last_name: str | None = Field(None, max_length=150)
    jersey_number: int | None = Field(None, ge=0, le=32767)
    role: Role | None = None
    position_label: str | None = Field(None, max_length=100)
    date_of_birth: datetime.date | None = None
    license_number: str | None = Field(None, max_length=100)
    dietary_restrictions: str | None = None
    lunch_days: list[datetime.date] | None = None


class LunchDaysIn(Schema):
    lunch_days: list[datetime.date]


# --- schedule -------------------------------------------------------------------------------------------------------------


class GenerateScheduleIn(Schema):
    match_duration_minutes: int | None = Field(None, ge=1, le=1440)


class RescheduleIn(Schema):
    field_id: uuid.UUID
    scheduled_start: datetime.datetime


class MatchTeamsPatch(Schema):
    """Only the keys that are sent change; ``null`` clears the slot back to TBD."""

    home_team_id: uuid.UUID | None = None
    away_team_id: uuid.UUID | None = None


class MatchCreateIn(Schema):
    home_team_id: uuid.UUID | None = None
    away_team_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    round_number: int | None = Field(None, ge=1, le=32767)
    round_label: str = Field("", max_length=100)
    field_id: uuid.UUID | None = None
    scheduled_start: datetime.datetime | None = None


class ResultIn(Schema):
    score_home: int = Field(ge=0, le=999)
    score_away: int = Field(ge=0, le=999)
    went_to_overtime: bool = False
    penalty_home: int | None = Field(None, ge=0, le=999)
    penalty_away: int | None = Field(None, ge=0, le=999)


class BlockIn(Schema):
    title: str = Field(min_length=1, max_length=255)
    starts_at: datetime.datetime
    ends_at: datetime.datetime
    field_id: uuid.UUID | None = None
    team_ids: list[uuid.UUID] = []


class BlockPatch(Schema):
    title: str | None = Field(None, min_length=1, max_length=255)
    starts_at: datetime.datetime | None = None
    ends_at: datetime.datetime | None = None
    field_id: uuid.UUID | None = None
    team_ids: list[uuid.UUID] | None = None


class LunchSettingsIn(Schema):
    duration_minutes: int = Field(ge=1, le=600)
    gap_before_minutes: int = Field(ge=0, le=600)
    gap_after_minutes: int = Field(ge=0, le=600)
    capacity_teams: int = Field(ge=1, le=1000)
    interval_minutes: int = Field(ge=0, le=600)


class LunchDayIn(Schema):
    date: datetime.date
    lunch_start: datetime.time | None = None
    lunch_end: datetime.time | None = None


class LunchIn(Schema):
    settings: LunchSettingsIn
    days: list[LunchDayIn] = []


class LunchGenerateIn(Schema):
    match_duration_minutes: int | None = Field(None, ge=1, le=600)


# --- fields, stages, break rules -----------------------------------------------------------------------------------------


class AvailabilityWindow(Schema):
    date: datetime.date
    start: datetime.time
    end: datetime.time


class FieldIn(Schema):
    name: str = Field(min_length=1, max_length=255)
    availability: list[AvailabilityWindow] = []


class FieldPatch(Schema):
    name: str | None = Field(None, min_length=1, max_length=255)
    availability: list[AvailabilityWindow] | None = None


class StageIn(Schema):
    name: str = Field(min_length=1, max_length=255)
    sequence: int | None = Field(None, ge=0, le=32767)
    format: StageFormat
    rounds: int | None = Field(None, ge=1, le=1000)
    num_groups: int | None = Field(None, ge=1, le=1000)
    placement: bool = False  # single elimination only: keep playing for every place, so each team ends with a final rank
    points_win: int = Field(3, ge=0, le=1000)
    points_draw: int = Field(1, ge=0, le=1000)
    points_loss: int = Field(0, ge=0, le=1000)
    tiebreaker_1: Tiebreaker = "goal_diff"
    tiebreaker_2: Tiebreaker | None = "goals_for"


class StagePatch(Schema):
    name: str | None = Field(None, min_length=1, max_length=255)
    sequence: int | None = Field(None, ge=0, le=32767)
    format: StageFormat | None = None
    rounds: int | None = Field(None, ge=1, le=1000)
    num_groups: int | None = Field(None, ge=1, le=1000)
    placement: bool | None = None
    points_win: int | None = Field(None, ge=0, le=1000)
    points_draw: int | None = Field(None, ge=0, le=1000)
    points_loss: int | None = Field(None, ge=0, le=1000)
    tiebreaker_1: Tiebreaker | None = None
    tiebreaker_2: Tiebreaker | None = None


class GenerateMatchesIn(Schema):
    with_placement: bool = False
    regenerate: bool = False  # throw away the stage's matches and groups first (only while nothing has been played and no later stage depends on them)


class SeedSlotIn(Schema):
    """One bracket slot: a direct team (``type="team"``, ``team_id``) or "Nth place of a group" (``type="group"``, ``group_id``, ``rank``)."""

    type: Literal["team", "group", "skip"] = "team"
    team_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    rank: int | None = Field(None, ge=1, le=1000)


class SeedIn(Schema):
    slots: list[SeedSlotIn]
    with_placement: bool = False


class NextPhaseIn(Schema):
    """Optional manual order per group of the stage: ``{group_id: [tournament_team_id, ...]}`` (best first). Groups left out keep their computed order."""

    order: dict[uuid.UUID, list[uuid.UUID]] = {}


class BreakRuleIn(Schema):
    field_id: uuid.UUID | None = None
    trigger_type: Trigger
    trigger_every_n_games: int | None = Field(None, ge=1, le=32767)
    trigger_every_n_minutes: int | None = Field(None, ge=1, le=32767)
    trigger_after_round_number: int | None = Field(None, ge=1, le=32767)
    trigger_window_start: datetime.time | None = None
    trigger_window_end: datetime.time | None = None
    gap_minutes: int | None = Field(None, ge=0, le=32767)
    replaces_default_gap: bool = True
    priority: int = Field(0, ge=0, le=32767)


class BreakRulePatch(Schema):
    field_id: uuid.UUID | None = None
    trigger_type: Trigger | None = None
    trigger_every_n_games: int | None = Field(None, ge=1, le=32767)
    trigger_every_n_minutes: int | None = Field(None, ge=1, le=32767)
    trigger_after_round_number: int | None = Field(None, ge=1, le=32767)
    trigger_window_start: datetime.time | None = None
    trigger_window_end: datetime.time | None = None
    gap_minutes: int | None = Field(None, ge=0, le=32767)
    replaces_default_gap: bool | None = None
    priority: int | None = Field(None, ge=0, le=32767)


# --- tasks and dressing rooms ---------------------------------------------------------------------------------------------


class TaskIn(Schema):
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    starts_at: datetime.datetime
    ends_at: datetime.datetime
    location_label: str = Field("", max_length=255)
    needed_quantity: int = Field(1, ge=1, le=32767)


class TaskPatch(Schema):
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    starts_at: datetime.datetime | None = None
    ends_at: datetime.datetime | None = None
    location_label: str | None = Field(None, max_length=255)
    needed_quantity: int | None = Field(None, ge=1, le=32767)


class TaskGenerateIn(Schema):
    title: str = Field(min_length=1, max_length=255)
    location_label: str = Field("", max_length=255)
    starts_at: datetime.datetime
    ends_at: datetime.datetime
    shift_minutes: int = Field(30, ge=5, le=1440)
    needed_quantity: int = Field(1, ge=1, le=32767)


class TaskAssignIn(Schema):
    member_id: uuid.UUID


class DressingRoomIn(Schema):
    name: str = Field(min_length=1, max_length=255)
    capacity: int | None = Field(None, ge=0, le=32767)
    notes: str = ""


class DressingRoomPatch(Schema):
    name: str | None = Field(None, min_length=1, max_length=255)
    capacity: int | None = Field(None, ge=0, le=32767)
    notes: str | None = None


class DressingAssignIn(Schema):
    dressing_room_id: uuid.UUID
    tournament_team_id: uuid.UUID
    assigned_from: datetime.datetime | None = None
    assigned_until: datetime.datetime | None = None
    note: str = ""


# --- public registration --------------------------------------------------------------------------------------------------


class PlayerRowIn(Schema):
    first_name: str = Field("", max_length=150)
    last_name: str = Field("", max_length=150)
    jersey_number: int | None = Field(None, ge=0, le=32767)
    role: Role = "player"
    position_label: str = Field("", max_length=100)
    date_of_birth: datetime.date | None = None
    license_number: str = Field("", max_length=100)
    dietary_restrictions: str = Field("", max_length=2000)
    lunch_days: list[datetime.date] = []


class PublicRegistrationIn(Schema):
    foreign_name: str = Field(min_length=1, max_length=255)
    foreign_contact_name: str = Field(min_length=1, max_length=255)
    foreign_contact_email: str = Field(min_length=3, max_length=254)
    foreign_contact_phone: str = Field("", max_length=32)
    foreign_primary_color: Color = ""
    foreign_secondary_color: Color = ""
    players: list[PlayerRowIn] = Field([], max_length=200)
    website: str = ""  # honeypot: left empty by people


class PlayersIn(Schema):
    players: list[PlayerRowIn] = Field(max_length=200)
