import datetime
import uuid
from decimal import Decimal

from ninja import Schema


class SignupMember(Schema):
    id: uuid.UUID
    name: str
    email: str
    date_of_birth: datetime.date | None = None
    has_login: bool


class SignupTeam(Schema):
    id: uuid.UUID
    name: str


class SignupRegistration(Schema):
    """Present when the sign-up came from the public registration page."""

    registration_id: uuid.UUID
    entry_kind: str
    invoice_number: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    requested_team_id: uuid.UUID | None = None
    requested_team_name: str | None = None
    requested_position_id: uuid.UUID | None = None
    requested_position_name: str | None = None
    requested_jersey_number: int | None = None
    product: str = ""


class SignupOut(Schema):
    membership_id: uuid.UUID
    member: SignupMember
    season_id: uuid.UUID
    season_name: str
    fee_status: str
    fee_amount: Decimal
    amount_paid: Decimal
    remaining_balance: Decimal
    signed_up_at: datetime.date | None = None
    #: Names of the active requirements not yet completed or bypassed.
    open_requirements: list[str]
    #: Fee paid or waived and no open requirement: can be approved right away.
    clean: bool
    teams: list[SignupTeam]
    registration: SignupRegistration | None = None


class SignupListOut(Schema):
    count: int
    clean_count: int
    season_id: uuid.UUID | None = None
    season_name: str | None = None
    results: list[SignupOut]


class SignupSummaryOut(Schema):
    season_id: uuid.UUID | None = None
    pending: int
    clean: int
    not_clean: int


class ApproveAllIn(Schema):
    season_id: uuid.UUID | None = None


class ApproveAllOut(Schema):
    approved: int


class PlaceIn(Schema):
    team_id: uuid.UUID
    position_id: uuid.UUID | None = None
    jersey_number: int | None = None


class PlaceOut(Schema):
    team: SignupTeam
    position: str | None = None
    jersey_number: int | None = None


class CancelOut(Schema):
    #: ``deleted`` (the person had no other ties and was removed) or ``cancelled`` (membership cancelled, person kept).
    result: str


class PositionOpt(Schema):
    id: uuid.UUID
    name: str


class SignupOptionsOut(Schema):
    teams: list[SignupTeam]
    positions: list[PositionOpt]


class DuplicateOut(Schema):
    member_id: uuid.UUID
    name: str
    email: str
    date_of_birth: datetime.date | None = None
    same_email: bool
    #: Already has a membership this season, so the sign-up cannot be merged into them.
    has_membership: bool


class DuplicatesOut(Schema):
    results: list[DuplicateOut]


class LinkIn(Schema):
    member_id: uuid.UUID
