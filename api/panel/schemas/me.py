"""Schemas of the management capability document (api/panel/routers/me.py)."""

import uuid

from ninja import Field, Schema

from api.panel.schemas.teams import SeasonOut


class MeMemberOut(Schema):
    id: uuid.UUID | None = Field(description="`null` for a platform superuser without a member record.")
    name: str


class MeClubOut(Schema):
    id: uuid.UUID
    name: str
    slug: str


class AreaOut(Schema):
    key: str = Field(description="Stable identifier of the management area, e.g. `members`, `shop`, `settings`.")
    label: str = Field(description="Translated display name.")
    feature: str | None = Field(description="The `club_features` key this area is licensed by, or `null` when it is always part of the product.")
    available: bool = Field(description="`false` when the area's feature is off for this club: the area does not exist there, whoever the caller is.")
    can_view: bool = Field(description="The caller may open the area (list and read screens).")
    can_manage: bool = Field(description="The caller may create, change or delete in the area. Implies `can_view`; finer per-object rules (a coach's own team) still apply on each endpoint.")


class MeOut(Schema):
    member: MeMemberOut
    club: MeClubOut
    season: SeasonOut | None = Field(description="The season covering today; `null` when none does.")
    roles: list[str] = Field(description="Club roles held (`admin`, `editor`, `member_admin`, ...), plus the derived `coach_manager`.")
    is_platform_superuser: bool
    club_features: dict[str, bool] = Field(description="Licensed modules, as in the member API's club context.")
    areas: list[AreaOut] = Field(description="Every management area, in navigation order. Build the menu from the entries with `can_view`.")
