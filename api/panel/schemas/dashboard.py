"""Schemas of the management overview (``GET /manage/home``, api/panel/routers/dashboard.py)."""

import datetime
import uuid
from decimal import Decimal

from ninja import Field, Schema

from api.protected.schemas import TeamRefOut


class HomeSeasonOut(Schema):
    id: uuid.UUID
    name: str
    start_date: datetime.date
    end_date: datetime.date


class HomeKpisOut(Schema):
    member_count: int = Field(description="Members this season.")
    member_count_change: int | None = Field(description="Against the season right before this one; `null` when there is none.")
    pending_approvals: int = Field(description="Memberships awaiting approval.")
    teams_without_manager: int = Field(description="Teams nobody can pick a squad for.")
    unrostered: int = Field(description="Active members on no team.")
    new_members: int = Field(description="Members in their first season at this club.")
    attendance_turnout: int | None = Field(description="Turnout this season in percent; `null` without events.")
    missing_documentation_count: int | None = Field(description="Members with an open onboarding checklist item; `null` when the club tracks no onboarding requirement (hide the tile).")


class HomeAttentionOut(Schema):
    pending_approvals: int
    teams_without_manager: int
    unrostered: int
    missing_documentation_count: int
    registrations_awaiting_count: int | None = Field(description="Registrations awaiting invoice confirmation. Club admins only (`null` otherwise).")


class HomeEventOut(Schema):
    id: uuid.UUID
    title: str
    start: datetime.datetime
    teams: list[TeamRefOut]


class HomeNewsOut(Schema):
    id: uuid.UUID
    title: str
    published_at: datetime.datetime


class HomeStatOut(Schema):
    label: str
    value: Decimal | int


class HomeGroupOut(Schema):
    title: str
    icon: str = Field(description="A Lucide icon name.")
    stats: list[HomeStatOut]


class HomeBillingOut(Schema):
    ends_at: datetime.date = Field(description="The current billing period ends then.")
    auto_renews: bool


class HomeSignupMonthOut(Schema):
    month: str
    new: int
    returning: int


class HomeFeeSliceOut(Schema):
    label: str
    value: int


class HomeAgingOut(Schema):
    label: str
    total: Decimal
    count: int
    overdue: bool


class HomeFinanceOut(Schema):
    signups: list[HomeSignupMonthOut] = Field(description="Signups per month, first-timers against returners.")
    fees: list[HomeFeeSliceOut] = Field(description="Membership fee status this season.")
    renewal_rate: int | None = Field(description="Percent of last season's active members who signed up again; `null` without a previous season.")
    outstanding: Decimal = Field(description="Unpaid shop orders, in euro.")
    unpaid_members: int = Field(description="Memberships with an unpaid fee this season.")
    aging: list[HomeAgingOut] = Field(description="Unpaid orders by age.")


class HomeOut(Schema):
    season: HomeSeasonOut | None = Field(description="The season covering today; `null` means the club is inert (no signup, no scheduling).")
    kpis: HomeKpisOut
    attention: HomeAttentionOut
    upcoming_events: list[HomeEventOut] = Field(description="The next events, narrowed to the caller's managed teams like the events list.")
    news: list[HomeNewsOut] = Field(description="The latest published news.")
    groups: list[HomeGroupOut] = Field(description="Statistic groups. The `Shop` group is for club admins only.")
    finance: HomeFinanceOut | None = Field(description="Charts and money figures. Club admins only (`null` otherwise).")
    billing: HomeBillingOut | None = Field(description="Set when the club's billing period ends soon and nothing is owed. Club admins only.")
    unread_notifications: int | None = Field(description="The caller's unread notifications in this club; `null` without a member profile.")
