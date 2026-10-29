"""Request and response shapes of the event extras (``api.panel.routers.event_extras``): tasks,
attachments, referee / official fees, the competition fetch and the fixture imports."""

import datetime
import uuid
from decimal import Decimal
from typing import Literal

from ninja import Field, Schema

from api.panel.schemas.events import PanelEventOut

# --- tasks -------------------------------------------------------------------------------------


class EventTaskOut(Schema):
    id: uuid.UUID
    title: str
    description: str
    needed_quantity: int
    claimed_count: int = Field(description="Slots already taken.")
    created_at: datetime.datetime


class EventTaskIn(Schema):
    title: str = Field(max_length=255)
    description: str = ""
    needed_quantity: int = Field(default=1, ge=1, description="How many people or families need to take this on.")


class EventTaskPatch(Schema):
    """Only what is sent changes."""

    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    needed_quantity: int | None = Field(default=None, ge=1)


class EventTaskListOut(Schema):
    items: list[EventTaskOut]


# --- attachments -------------------------------------------------------------------------------


class EventAttachmentOut(Schema):
    id: uuid.UUID
    name: str
    size: int = Field(description="Bytes.")
    uploaded_by: str | None = Field(description="Name of the member who uploaded it.")
    created: datetime.datetime


class EventAttachmentListOut(Schema):
    items: list[EventAttachmentOut]


# --- fees --------------------------------------------------------------------------------------


class FeeRowOut(Schema):
    kind: Literal["referee", "official"]
    id: uuid.UUID = Field(description="The assignment's id (not the member's).")
    name: str
    fee: Decimal
    km: Decimal | None
    km_rate: Decimal | None
    no_fee: bool
    total_payable: Decimal = Field(description="Fee plus the travel reimbursement.")


class FeeListOut(Schema):
    items: list[FeeRowOut]


class FeeRowIn(Schema):
    kind: Literal["referee", "official"]
    id: uuid.UUID = Field(description="The referee or official assignment on this event.")
    fee: Decimal | None = Field(default=None, ge=0, description="`null` clears it (0).")
    km: Decimal | None = Field(default=None, ge=0)
    km_rate: Decimal | None = Field(default=None, ge=0)
    no_fee: bool = Field(default=False, description="Unpaid: the fee and travel are dropped.")


class FeesIn(Schema):
    fees: list[FeeRowIn] = Field(min_length=1, description="Rows not listed are left alone; a row that didn't change is not written.")


class FeesOut(Schema):
    saved: list[str] = Field(description="Names whose fee line was changed.")
    items: list[FeeRowOut]


# --- competition fetch -------------------------------------------------------------------------


class FetchGameInfoOut(Schema):
    fetched: bool = Field(description="False when the event's competition is not enabled for this club (nothing was fetched).")
    event: PanelEventOut


# --- fixture imports ---------------------------------------------------------------------------

ImportSource = Literal["rbihf", "dfel", "ijnl"]


class ImportSourceOut(Schema):
    source: ImportSource
    name: str
    enabled: bool = Field(description="The club has this importer switched on.")


class ImportSourcesOut(Schema):
    sources: list[ImportSourceOut]
    json_import: bool = Field(description="The JSON play-day import (always available to admins).")
    recheck: bool = Field(description="At least one importer is on, so stored fixture lists can be re-checked.")


class ImportPreviewIn(Schema):
    url: str = Field(description="The team page on the federation's site.")
    team_id: uuid.UUID
    season_id: uuid.UUID
    competition_label: str = ""


class ImportApplyIn(ImportPreviewIn):
    locations: dict[str, str] = Field(default_factory=dict, description="`external_game_id` -> location id for each game to create or update; blank or missing: no location.")
    opponents: dict[str, str] = Field(default_factory=dict, description="`external_game_id` -> opponent id; blank or missing: the scraped name is matched or created.")


class FixtureOut(Schema):
    external_game_id: str
    start: datetime.datetime
    is_home: bool
    opponent_name: str
    venue_text: str


class PlannedCreateOut(Schema):
    fixture: FixtureOut
    suggested_location_id: uuid.UUID | None
    suggested_opponent_id: uuid.UUID | None


class PlannedUpdateOut(PlannedCreateOut):
    event_id: uuid.UUID
    changes: dict[str, list[str | None]] = Field(description="Field -> `[old, new]`, display-ready.")


class PlannedDeleteOut(Schema):
    event_id: uuid.UUID
    title: str
    start: datetime.datetime
    external_game_id: str


class ChoiceOut(Schema):
    id: uuid.UUID
    name: str


class ImportPlanOut(Schema):
    source: str
    scraped_team_name: str
    source_id: str
    to_create: list[PlannedCreateOut]
    to_update: list[PlannedUpdateOut]
    to_delete: list[PlannedDeleteOut]
    unchanged_count: int
    location_choices: list[ChoiceOut]
    opponent_choices: list[ChoiceOut]


class ImportPreviewOut(Schema):
    plan: ImportPlanOut


class RecheckSectionOut(Schema):
    team_id: uuid.UUID
    team_name: str
    source: str
    url: str
    competition_label: str
    error: str = Field(description="Set when the list could not be fetched; then `plan` is null.")
    plan: ImportPlanOut | None


class RecheckPreviewOut(Schema):
    season_id: uuid.UUID | None = Field(description="Null when the club has no season.")
    team_id: uuid.UUID | None
    sections: list[RecheckSectionOut]
    has_changes: bool


class RecheckChoice(Schema):
    team_id: uuid.UUID
    source: str = Field(description="`RBIHF`, `DFEL` or `IJNL`, as in the preview section.")
    competition_label: str = ""
    locations: dict[str, str] = Field(default_factory=dict)
    opponents: dict[str, str] = Field(default_factory=dict)


class RecheckApplyIn(Schema):
    season_id: uuid.UUID | None = Field(default=None, description="Default: the current season.")
    team_id: uuid.UUID | None = Field(default=None, description="Only this team; default every team.")
    choices: list[RecheckChoice] = Field(default_factory=list)


class JSONPlannedGameOut(Schema):
    external_game_id: str
    start: datetime.datetime
    end: datetime.datetime | None
    city: str
    team_ids: list[uuid.UUID]
    team_names: str
    hosted_by_us: bool
    label: str
    is_update: bool
    changes: dict[str, list[str]]
    suggested_location_id: uuid.UUID | None
    warning: str | None = Field(description="The suggested location contradicts who hosts the day.")


class JSONImportPreviewOut(Schema):
    source: str
    competition: str
    title: str
    games: list[JSONPlannedGameOut]
    unchanged_count: int
    location_choices: list[ChoiceOut]


class JSONImportOut(Schema):
    created: int
    updated: int
