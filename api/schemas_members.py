"""Request / response schemas of the members API. Ids are strings (UUIDs); nothing secret is ever exposed."""

import datetime
import uuid
from decimal import Decimal
from typing import Annotated, Literal

from ninja import Field, Schema
from pydantic import BeforeValidator

from api.schemas_public_registration import InstallmentOut

#: A UUID (or other primary key) rendered as a string.
UID = Annotated[str, BeforeValidator(str)]
RoleName = Literal["member", "guardian", "staff", "none"]


# -- members ---------------------------------------------------------------------------------------------------------


class MemberFilters(Schema):
    q: str | None = Field(None, description="Search first/last name (each word), email and license number.")
    role: RoleName | None = Field(None, description="Only people holding this derived role (a person can hold several), or none = holding no role in the season (not a member then, not staff then, not a guardian).")
    season: uuid.UUID | None = Field(None, description="Only people with a club membership in this season.")
    status: str | None = Field(None, description="Club membership status (active, pending, lapsed, cancelled, or open = active or pending) in the season (default: the current one).")
    fee_status: str | None = Field(None, description="Fee status (unpaid, paid, partially_paid, waived) of that membership.")
    team: str | None = Field(None, description='A team id: on this team\'s roster or staff in the season (default: the current one); or "none": on no team at all that season.')
    include_guardians: bool = Field(False, description="Also list people who are only a guardian or only staff. Implied by role=guardian|staff.")
    ordering: str = Field("last_name", description="One of last_name, first_name, date_of_birth, license_number, email; prefix with - to reverse.")


class FamilyRefOut(Schema):
    id: UID
    name: str


class MemberSummary(Schema):
    id: UID
    first_name: str
    last_name: str
    email: str
    date_of_birth: datetime.date | None = None
    language: str
    license_number: str | None = None
    photo_url: str | None = None
    roles: list[str]
    family_ids: list[UID]
    has_login: bool = False
    #: The families' display names (a family without a name is shown by its people's surnames).
    families: list[FamilyRefOut] = []
    #: Membership status in the listed season (None if they hold no membership then). Fee status: admins only.
    status: str | None = None
    fee_status: str | None = None

    @staticmethod
    def resolve_roles(obj) -> list[str]:
        if isinstance(obj, dict):
            return obj["roles"]
        if hasattr(obj, "is_member_flag"):  # annotated by the list endpoint: no extra queries
            flags = (("member", obj.is_member_flag), ("guardian", obj.is_guardian_flag), ("staff", obj.is_staff_flag))
            return [role for role, on in flags if on]
        return sorted(str(r.value) for r in obj.roles)

    @staticmethod
    def resolve_family_ids(obj) -> list[str]:
        if isinstance(obj, dict):
            return obj["family_ids"]
        return sorted({str(link.family_id) for link in obj.family_memberships.all()})

    @staticmethod
    def resolve_has_login(obj) -> bool:
        if isinstance(obj, dict):
            return bool(obj.get("has_login"))
        return obj.user_id is not None

    @staticmethod
    def resolve_families(obj) -> list[dict]:
        if isinstance(obj, dict):
            return obj.get("families", [])
        out = {}
        for link in obj.family_memberships.all():
            family = link.family
            if family.pk not in out:
                surnames = sorted({m.member.last_name for m in family.memberships.all() if m.member.last_name})
                out[family.pk] = {"id": family.pk, "name": family.name or " / ".join(surnames) or f"Family {str(family.pk)[:8]}"}
        return sorted(out.values(), key=lambda f: f["name"].lower())


class MemberFamilyOut(Schema):
    id: UID
    name: str
    role: str


class MemberGroupOut(Schema):
    id: UID
    name: str


class ClubMembershipOut(Schema):
    id: UID
    season_id: UID
    status: str
    # Admin only (null for everyone else):
    fee_status: str | None = None
    fee_amount: Decimal | None = None
    amount_paid: Decimal | None = None
    early_payment_deadline: datetime.date | None = None
    early_payment_discount: Decimal | None = None
    signed_up_at: datetime.date | None = None
    activated_at: datetime.date | None = None
    next_installment: InstallmentOut | None = None


class StaffAssignmentOut(Schema):
    id: UID
    team_id: UID
    team_name: str
    season_id: UID
    position: str


class TeamMembershipOut(Schema):
    id: UID
    team_id: UID
    team_name: str
    season_id: UID
    position: str | None = None
    jersey_number: int | None = None


class MemberDetail(MemberSummary):
    photo_public_consent: bool
    profile_photo_url: str | None = None
    player_photo_url: str | None = None  # admins only
    # Contact details; null when the viewer may not see them (see the endpoint docstring).
    phone: str | None = None
    emergency_phone: str | None = None
    phone_display: str | None = None  # the same numbers formatted with spaces, for showing (not for forms or tel: links)
    emergency_phone_display: str | None = None
    families: list[MemberFamilyOut]
    groups: list[MemberGroupOut]  # admin only, empty otherwise
    club_memberships: list[ClubMembershipOut]
    staff_assignments: list[StaffAssignmentOut]
    team_memberships: list[TeamMembershipOut]


class MemberIn(Schema):
    first_name: str
    last_name: str
    email: str = ""
    phone: str | None = None
    emergency_phone: str | None = None
    photo_public_consent: bool = False
    date_of_birth: datetime.date | None = None
    language: str = ""
    license_number: str | None = None


class MembershipPartIn(Schema):
    """Sign the new person up for a season while adding them. ``active`` skips the sign-up queue (staff-added people, no fee needed);
    ``pending`` sends them to the queue with ``fee_amount`` (or ``waived``)."""

    season_id: uuid.UUID | None = Field(None, description="Default: the current season.")
    status: Literal["active", "pending"] = "active"
    fee_amount: Decimal = Decimal("0.00")
    waived: bool = False


class FamilyPartIn(Schema):
    family_id: uuid.UUID
    role: Literal["parent", "child", "guardian", "other"] = "child"


class GuardianIn(Schema):
    """A guardian for the new person: an existing member (``member_id``), or a new one (names, optional email). Becomes a parent in the person's family."""

    member_id: uuid.UUID | None = None
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    invite: bool = False


class InvitePartIn(Schema):
    """Give the new person a login (needs an email address) and email them a link to set a password."""

    send: bool = True
    role_slug: str = "member"


class MemberCreateIn(MemberIn):
    """``POST /members``. The extra parts are all optional, so a plain member payload still works exactly as before."""

    membership: MembershipPartIn | None = None
    family: FamilyPartIn | None = Field(None, description="Join an existing family with a role.")
    guardians: list[GuardianIn] = Field(default_factory=list, description="Without ``family``: a new family is made with the person as a child and these as parents. With it: they are added to that family.")
    invite: InvitePartIn | None = None


class InviteResultOut(Schema):
    sent: bool
    user_created: bool
    email: str


class MemberCreateOut(MemberDetail):
    membership_id: uuid.UUID | None = None
    guardian_ids: list[uuid.UUID] = Field(default_factory=list)
    invite: InviteResultOut | None = None
    guardian_invites: list[InviteResultOut] = Field(default_factory=list)


class MemberPatch(Schema):
    """Only the fields that are sent are changed."""

    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    emergency_phone: str | None = None
    photo_public_consent: bool | None = None
    date_of_birth: datetime.date | None = None
    language: str | None = None
    license_number: str | None = None


class GrantLoginIn(Schema):
    role_slug: str = "member"


class GrantLoginOut(Schema):
    member_id: UID
    user_id: UID
    email: str
    user_created: bool
    email_sent: bool = False  # false when the automated-email pause is on or sending failed


# -- families --------------------------------------------------------------------------------------------------------


class FamilyMemberOut(Schema):
    member_id: UID
    first_name: str
    last_name: str
    role: str
    email: str = ""
    has_login: bool = False


class FamilyOut(Schema):
    id: UID
    name: str
    display_name: str
    members: list[FamilyMemberOut]


class FamilyMemberIn(Schema):
    member_id: uuid.UUID
    role: str = "parent"


class FamilyIn(Schema):
    name: str = ""
    members: list[FamilyMemberIn] = []


class FamilyPatch(Schema):
    name: str


class FamilyRolePatch(Schema):
    role: str


# -- groups ----------------------------------------------------------------------------------------------------------


class GroupMemberOut(Schema):
    member_id: UID
    first_name: str
    last_name: str
    email: str = ""
    is_admin: bool = False
    must_respond: bool = True


class GroupOut(Schema):
    id: UID
    name: str
    member_count: int
    members: list[GroupMemberOut] = []
    can_manage: bool = False  # rename / delete / appoint admins (members managers); the group's admins manage its members
    can_manage_members: bool = False


class GroupMemberIn(Schema):
    is_admin: bool = False
    must_respond: bool = True


class GroupMemberPatch(Schema):
    is_admin: bool | None = None
    must_respond: bool | None = None


class GroupIn(Schema):
    name: str


# -- audit history ---------------------------------------------------------------------------------------------------


class HistoryFilters(Schema):
    actor: str | None = Field(None, description="Id of the user who made the change.")
    action: str | None = Field(None, description="Action name or prefix, e.g. member.updated or family.")
    target_type: str | None = Field(None, description="member, family, group, ...")
    since: datetime.datetime | None = Field(None, description="Only entries created at or after this moment.")


class HistoryEntryOut(Schema):
    id: UID
    created: datetime.datetime
    actor_id: UID | None = None
    actor_email: str | None = None
    member_id: UID | None = None
    action: str
    target_type: str
    target_id: str
    target_label: str
    changes: dict
