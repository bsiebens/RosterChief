"""Shapes of the management referee / official assignment routes (``/manage/events/{id}/referees|officials``)."""

import datetime
import uuid

from ninja import Field, Schema


class CrewAssignedOut(Schema):
    id: uuid.UUID = Field(description="The assignment (use it to unassign).")
    member_id: uuid.UUID | None = Field(description="Null for an external person logged by name only.")
    name: str
    external: bool
    assigned_by: str | None = Field(description="Who made the assignment; null for a self-service one.")


class CrewCandidateOut(Schema):
    member_id: uuid.UUID
    name: str
    expired: bool = Field(description="Their referee validity has lapsed (staff may still pick them). Always false for officials.")
    has_conflict: bool = Field(description="They are expected at another event at an overlapping time; informational only.")
    conflict_titles: str = Field(description="The overlapping events' titles, comma separated.")


class CrewSignupOut(Schema):
    member_id: uuid.UUID
    name: str
    status: str = Field(description="`invited` (no answer yet), `accepted` (signed up, waiting for staff) or `declined`.")
    responded_at: datetime.datetime | None


class CrewSignupsOut(Schema):
    interested: list[CrewSignupOut] = Field(description="Signed up, waiting for staff to confirm.")
    waiting: list[CrewSignupOut] = Field(description="Invited, no answer yet.")
    declined: list[CrewSignupOut]


class CrewPanelOut(Schema):
    needed: bool = Field(description="False when the game is not one the club arranges this crew for (not a home game, or federation-managed); the rest is then empty.")
    max: int = Field(description="Capacity of the game.")
    full: bool
    can_manage: bool = Field(description="The caller may assign and unassign (club admin, member admin or crew manager).")
    assigned: list[CrewAssignedOut]
    candidates: list[CrewCandidateOut] = Field(description="Who could be assigned; empty unless `can_manage` and the game has room.")
    signups: CrewSignupsOut | None = Field(description="Not-yet-confirmed sign-ups; null unless `can_manage`.")


class CrewAssignIn(Schema):
    member_id: uuid.UUID = Field(description="One of the panel's `candidates`.")


class CrewExternalIn(Schema):
    name: str = Field(max_length=255, description="A person who is not a club member, e.g. federation-appointed.")
