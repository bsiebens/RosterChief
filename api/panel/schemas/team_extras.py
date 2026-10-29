"""Schemas of the team-extras routes (api/panel/routers/team_extras.py): reorder, bulk-add, photos, analytics, fixture sources."""

import datetime
import uuid
from typing import Literal

from ninja import Field, Schema

# --- reorder -----------------------------------------------------------------------------


class ReorderIn(Schema):
    ids: list[uuid.UUID] = Field(description="The team ids of the page, in the order now shown. They take the slots they held in the club's order.")


class ReorderOut(Schema):
    ids: list[uuid.UUID] = Field(description="Every team of the club in the new, stored order (numbered 1..N).")


# --- bulk add ----------------------------------------------------------------------------


class BulkRowIn(Schema):
    member_id: uuid.UUID
    position_id: uuid.UUID = Field(description="A staff position makes the row a staff assignment, any other a player.")
    jersey_number: int | None = Field(None, ge=0, description="Players only.")
    is_captain: bool = Field(False, description="Players only.")
    is_alternate_captain: bool = Field(False, description="Players only.")


class BulkAddIn(Schema):
    season_id: uuid.UUID | None = Field(None, description="Defaults to the current season.")
    rows: list[BulkRowIn] = Field(min_length=1, max_length=200)


class BulkAddOut(Schema):
    players_added: int
    staff_added: int


# --- photo -------------------------------------------------------------------------------


class TeamPhotoOut(Schema):
    season_id: uuid.UUID
    url: str | None = Field(description="`null` when the team has no photo for the season.")


# --- analytics ---------------------------------------------------------------------------


class AnalyticsOut(Schema):
    season_id: uuid.UUID | None = Field(description="`null` when the club has no season to show.")
    scope: str = Field(description="`all`, `home`, `away` or `last5`.")
    friendlies: bool
    competition: str = Field(description='The competition applied; "" for all (an unknown one is dropped).')
    analytics: dict | None = Field(
        description=(
            "What the web analytics screen shows (events.services.game_stats.team_analytics): `competitions`, `games`, `record`, `goals_for`, `goals_against`, `form`, `split` (home/away), "
            "`periods`, `goal_minutes`, `top_scorers` / `top_assists` / `top_points` / `top_offenders` (`{name, number, member_id, total, pct}`), `goalies`, `opponents`, `game_rows`, "
            "`strength_warnings` and more. Games appear as `{id, title, start}`. `null` without a season."
        )
    )


class StrengthIn(Schema):
    event_id: uuid.UUID = Field(description="The game.")
    goal: str = Field(description="The goal's id in the game timeline (`strength_warnings[].goal`).")
    strength: str = Field(description="`EQ`, `PP1`, `SH1`, ... one of the strengths the web screen offers.")


class StrengthOut(Schema):
    ok: bool = True


# --- fixture sources ---------------------------------------------------------------------


class FixtureSourceOut(Schema):
    id: str = Field(description="Stable id of (source, season, competition label).")
    source: str
    url: str
    season_id: uuid.UUID | None
    season_name: str
    season_exists: bool
    competition_label: str
    recorded_at: datetime.datetime | None
    last_checked_at: datetime.datetime | None
    last_check: dict | None = Field(description="`{rescheduled, new_games, removed_games, error}` from the last check.")


class FixtureSourceListOut(Schema):
    items: list[FixtureSourceOut]


class FixtureSourceIn(Schema):
    source: Literal["RBIHF", "DFEL", "IJNL"]
    url: str = Field(min_length=1, max_length=500)
    season_id: uuid.UUID
    competition_label: str = Field("", max_length=200, description="Empty when the team has one competition. Re-posting the same (source, season, label) replaces the URL.")


class FixtureSourcePatch(Schema):
    url: str | None = Field(None, min_length=1, max_length=500)
    competition_label: str | None = Field(None, max_length=200)


class FixtureSyncIn(Schema):
    season_id: uuid.UUID | None = Field(None, description="Defaults to the current season.")
    apply: bool = Field(False, description="False only checks and records what differs. True also creates, updates and deletes the team's games (no location choices).")
