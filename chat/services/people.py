"""Who somebody is to the club right now, resolved at read time for the current season (never stored).

Staff beats player beats parent. ``admin`` is a club admin without a team role; everyone else is ``other``."""

import datetime

from django.utils import timezone

from authorization.permissions import is_club_admin
from events.models import Season
from members.models import FamilyMembership, Member
from teams.models import StaffAssignment, TeamMembership

ADULT_AGE = 18
FORMER_MEMBER = "Former member"


def current_season():
    return Season.get_current()


def is_minor(member: Member, *, today: datetime.date | None = None) -> bool:
    """Under 18 by date of birth; without one, a member who is a child in a family counts as a minor."""
    if member.date_of_birth:
        today = today or timezone.localdate()
        years = today.year - member.date_of_birth.year - ((today.month, today.day) < (member.date_of_birth.month, member.date_of_birth.day))
        return years < ADULT_AGE
    return FamilyMembership.objects.filter(member=member, role=FamilyMembership.FamilyRole.CHILD).exists()


def minor_ids(members) -> set:
    return {m.pk for m in members if is_minor(m)}


def photo_url(member: Member):
    if member.profile_photo or (member.player_photo and member.photo_public_consent):
        return member.photo_url
    return None


def resolve(members, *, team=None) -> dict:
    """``{member_id: Person}`` for ``members`` (Member objects). ``team`` picks the staff position shown as the label."""
    members = [m for m in members if m is not None]
    ids = [m.pk for m in members]
    season = current_season()
    staff, players, parents = {}, set(), set()
    if season is not None and ids:
        for sa in StaffAssignment.objects.filter(member_id__in=ids, season=season).select_related("position").order_by("position__ordering", "position__name"):
            staff.setdefault(sa.member_id, []).append(sa)
        players = set(TeamMembership.objects.filter(member_id__in=ids, season=season).values_list("member_id", flat=True))
    if ids:
        parents = set(Member.objects.guardians().filter(pk__in=ids).values_list("pk", flat=True))
    out = {}
    for m in members:
        role, label = "other", "Member"
        if m.pk in staff or m.club_staff:
            role = "staff"
            sas = staff.get(m.pk, [])
            chosen = next((sa for sa in sas if team is not None and sa.team_id == team.pk), None) or (sas[0] if sas else None)
            label = chosen.position.name if chosen else "Staff"
        elif m.pk in players:
            role, label = "player", "Player"
        elif m.pk in parents:
            role, label = "parent", "Parent"
        elif m.user_id and is_club_admin(m.user):
            role, label = "admin", "Club admin"
        out[m.pk] = {"member_id": m.pk, "name": m.get_full_name(), "role": role, "role_label": label, "photo_url": photo_url(m)}
    return out


def former_person() -> dict:
    return {"member_id": None, "name": FORMER_MEMBER, "role": "other", "role_label": "", "photo_url": None}


def is_staffish(person: dict) -> bool:
    return person["role"] in ("staff", "admin")
