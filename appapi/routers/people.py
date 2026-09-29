"""Profiles of the account's managed people and member photos (API.md §7.2, §8), club
host. Editing goes through mobile.forms.MemberProfileForm, the member web app's own
"Edit info" form, so validation and phone parsing are identical."""

import mimetypes
import uuid

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from ninja import Router

from club.models import ClubMembership
from club.services.access import current_season
from club.services.onboarding import checklist_for
from members.models import FamilyMembership, Member
from members.services.photos import can_view_member_photo
from mobile.forms import MemberProfileForm
from rosterchief.media import MEDIA_CSP
from teams.models import TeamMembership

from ..errors import ApiError
from ..presenters import member_photo_url, team_ref
from ..schemas import ErrorOut, ProfileOut, ProfilePatchIn
from ..scope import club_scope

router = Router(tags=["people"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
#: Fields MemberProfileForm edits that the JSON API does too (the photo has its own upload, phase 3).
EDITABLE = ("first_name", "last_name", "date_of_birth", "email", "phone", "emergency_phone", "photo_public_consent")


def _profile(request, club, member: Member) -> dict:
    season = current_season(club)
    guardians = (
        FamilyMembership.objects.filter(
            role__in=[FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN],
            family__club=club,
            family__memberships__member=member,
            family__memberships__role=FamilyMembership.FamilyRole.CHILD,
        )
        .select_related("member")
        .distinct()
    )
    teams, open_requirements = [], []
    if season is not None:
        teams = [
            {"team": team_ref(membership.team), "position": membership.position.name if membership.position_id else None, "jersey_number": membership.jersey_number}
            for membership in TeamMembership.objects.filter(member=member, season=season, team__club=club).select_related("team", "position").order_by("team__name")
        ]
        membership = ClubMembership.objects.filter(club=club, member=member, season=season).first()
        if membership is not None:
            open_requirements = [requirement.name for requirement, status in checklist_for(membership) if status is None or not (status.is_complete or status.is_bypassed)]
    return {
        "id": member.pk,
        "first_name": member.first_name,
        "last_name": member.last_name,
        "date_of_birth": member.date_of_birth,
        "email": member.email,
        "phone": str(member.phone) if member.phone else None,
        "emergency_phone": str(member.emergency_phone) if member.emergency_phone else None,
        "photo_url": member_photo_url(request, member),
        "photo_public_consent": member.photo_public_consent,
        "guardians": [{"first_name": row.member.first_name, "last_name": row.member.last_name, "role": row.role} for row in guardians],
        "teams": teams,
        "open_requirements": open_requirements,
    }


@router.get("/members/{member_id}", response={200: ProfileOut, **ERRORS}, summary="A managed person's profile")
def profile(request, member_id: uuid.UUID):
    """Only the account's own person and their children (`404` for anyone else)."""
    scope = club_scope(request)
    return _profile(request, scope.club, scope.person(member_id))


def _form_data(member: Member) -> dict:
    data = {
        "first_name": member.first_name,
        "last_name": member.last_name,
        "date_of_birth": member.date_of_birth.isoformat() if member.date_of_birth else "",
        "email": member.email,
        "phone": str(member.phone) if member.phone else "",
        "emergency_phone": str(member.emergency_phone) if member.emergency_phone else "",
    }
    if member.photo_public_consent:
        data["photo_public_consent"] = "on"
    return data


@router.patch("/members/{member_id}", response={200: ProfileOut, **ERRORS, 422: ErrorOut}, summary="Edit a managed person's profile")
def update_profile(request, member_id: uuid.UUID, payload: ProfilePatchIn):
    """Send only the fields that change. `422` lists invalid fields (e.g. a phone
    number that can't be parsed)."""
    scope = club_scope(request)
    member = scope.person(member_id)
    data = _form_data(member)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "photo_public_consent":
            data.pop(field, None)
            if value:
                data[field] = "on"
        elif field == "date_of_birth":
            data[field] = value.isoformat() if value else ""
        else:
            data[field] = value or ""
    form = MemberProfileForm(data, instance=member)
    if not form.is_valid():
        fields = {name: [str(message) for message in messages] for name, messages in form.errors.items() if name in EDITABLE or name == "__all__"}
        raise ApiError(422, "validation_error", fields=fields or {"__all__": [str(message) for messages in form.errors.values() for message in messages]})
    form.save()
    member.refresh_from_db()
    return _profile(request, scope.club, member)


@router.get("/members/{member_id}/photo", response={200: None, **ERRORS}, summary="A member's photo", openapi_extra={"responses": {200: {"description": "The image.", "content": {"image/*": {}}}}})
def photo(request, member_id: uuid.UUID):
    """The same rule as the website: the member, their parents/guardians, staff who may
    see them, or anyone when the photo is public. Anyone else gets `404`."""
    scope = club_scope(request)
    member = get_object_or_404(Member, pk=member_id)
    if not member.photo or not can_view_member_photo(request.user, member, scope.club):
        raise ApiError(404, "not_found")
    try:
        handle = member.photo.open("rb")
    except FileNotFoundError as error:
        raise ApiError(404, "not_found") from error
    content_type = mimetypes.guess_type(member.photo.name)[0] or "application/octet-stream"
    if not content_type.startswith("image/") or content_type == "image/svg+xml":
        content_type = "application/octet-stream"
    response = FileResponse(handle, content_type=content_type)
    # The URL is versioned (presenters.member_photo_url), so the app may keep it an hour.
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = MEDIA_CSP
    return response
