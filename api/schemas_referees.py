"""Schemas for the referee management API (``/referee-management``)."""

import datetime
from decimal import Decimal
from uuid import UUID

from ninja import Schema


class ConflictOut(Schema):
    event_id: UUID
    title: str
    start: datetime.datetime
    end: datetime.datetime
    kind: str
    role: str  # attendance | referee | official


class AssignmentOut(Schema):
    id: UUID
    member_id: UUID | None = None
    name: str
    is_external: bool
    type_id: UUID | None = None
    type_name: str
    fee: Decimal
    km: Decimal | None = None
    km_rate: Decimal | None = None
    no_fee: bool
    fee_pending: bool
    total: Decimal
    conflicts: list[ConflictOut] = []


class TeamRefOut(Schema):
    id: UUID
    name: str
    short_name: str


class SignupOut(Schema):
    id: UUID
    kind: str
    member_id: UUID
    name: str
    created: datetime.datetime
    can_assign: bool = False
    conflicts: list[ConflictOut] = []


class GameOut(Schema):
    id: UUID
    title: str
    kind: str
    start: datetime.datetime
    end: datetime.datetime
    location_name: str
    opponent_name: str
    teams: list[TeamRefOut]
    referees: list[AssignmentOut]
    officials: list[AssignmentOut]
    max_referees: int
    max_officials: int
    needs_referees: bool
    needs_officials: bool
    referee_short: bool
    official_short: bool
    signups: list[SignupOut] = []
    fees_pending: bool
    conflict_count: int = 0
    total: Decimal


class GamesOut(Schema):
    games: list[GameOut]
    counts: dict[str, int]
    range: str
    need: str
    scope: str


class KindKpiOut(Schema):
    games: int
    none: int
    partial: int
    full: int


class WorkloadOut(Schema):
    key: str
    member_id: UUID | None = None
    name: str
    kind: str
    games: int
    total: Decimal


class TypeOut(Schema):
    id: UUID
    name: str


class SummaryOut(Schema):
    season_name: str
    kpis: dict[str, KindKpiOut]
    fees_pending: int
    workload: list[WorkloadOut]
    types: dict[str, list[TypeOut]]


class CandidateOut(Schema):
    id: UUID
    name: str
    level: str
    expired: bool
    eligible: bool = True
    signed_up: bool = False
    conflicts: list[ConflictOut]


class AssignIn(Schema):
    kind: str
    member_id: UUID | None = None
    external_name: str = ""
    referee_type_id: UUID | None = None
    force: bool = False  # assign although the person has another event at the same time


class SignupIn(Schema):
    kind: str
    member_id: UUID


class AssignmentPatch(Schema):
    fee: Decimal | None = None
    km: Decimal | None = None
    km_rate: Decimal | None = None
    no_fee: bool | None = None
    referee_type_id: UUID | None = None
    name: str | None = None


class CapacityIn(Schema):
    max_referees: int | None = None
    max_officials: int | None = None
