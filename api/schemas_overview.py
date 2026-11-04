"""Schemas for ``/api/v1/overview`` (the management app's home page)."""

import uuid

from ninja import Schema


class SeasonRef(Schema):
    id: uuid.UUID
    name: str


class KpisOut(Schema):
    total_members: int
    previous_total_members: int | None = None
    awaiting_approval: int
    teams_without_coach: int
    unrostered_members: int
    new_members: int
    previous_new_members: int | None = None


class MonthOut(Schema):
    month: str  # YYYY-MM
    new: int
    returning: int


class RenewalOut(Schema):
    previous_season: str
    renewed: int
    of: int
    rate: int
    previous_rate: int | None = None  # the same figure one season earlier


class PeopleCard(Schema):
    admins: int
    staff: int
    members: int
    guardians: int


class MembersCard(Schema):
    total: int
    active: int
    pending: int
    lapsed: int


class TeamsCard(Schema):
    teams: int
    players: int
    staff: int


class EventsCard(Schema):
    upcoming: int
    this_season: int


class CardsOut(Schema):
    people: PeopleCard
    members: MembersCard
    teams: TeamsCard
    events: EventsCard


class OverviewOut(Schema):
    season: SeasonRef | None = None
    previous_season: SeasonRef | None = None
    kpis: KpisOut | None = None
    signups: list[MonthOut] = []
    fees: dict[str, int] | None = None  # only for holders of members.manage_members
    renewal: RenewalOut | None = None  # idem
    cards: CardsOut | None = None
