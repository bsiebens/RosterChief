"""Management API: onboarding, sign-ups, eligibility and the player photo (API.md §17.3, phase A).

The JSON counterpart of these ``/manage`` screens (management/views.py):

* onboarding requirement settings: ``/manage/onboarding-requirements`` (list, create, edit, delete);
* a member's checklist for the current season: ``/manage/members/{id}/requirements`` (read: staff who
  may see the member) with complete (multipart, optional document), bypass, incomplete and the
  document download (writes: member admins);
* the sign-up queue: ``/manage/signup`` and its approve / approve-all-clean / place-in-team / cancel /
  link actions (member admins);
* referee and official eligibility of a member (``/manage/members/{id}/referee-eligibility``,
  ``/official-eligibility``; officials need the club's ``officials`` feature) and the player photo
  (``/manage/members/{id}/photo``, multipart).

Who may do what is the web's rule: ``can_manage_members`` for writes, ``members_visible_to`` for
reading a member. Logic is shared with the web views: club.services.onboarding / club.services.signup /
club.services.cancellation / club.services.signup_linking, teams.services.eligibility and
members.services.photos; validation is management.forms.
"""

import uuid

from django.core.exceptions import ValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from ninja import File, Form, Router, UploadedFile

from api.core.errors import ApiError
from api.core.forms import form_error
from api.core.schemas import ErrorOut
from api.core.uploads import IMAGE_TYPES, PDF_TYPES, validate_upload
from api.panel.guards import panel_club, require
from api.panel.routers.members import member_detail
from api.panel.schemas import members as member_schemas
from api.panel.schemas import members_onboarding as s
from api.protected.services import club_features
from club.models import ClubMembership, MemberRequirementStatus, OnboardingRequirement
from club.services.access import can_manage_members, current_season, members_visible_to
from club.services.cancellation import cancel_membership
from club.services.onboarding import approve_all_clean, approve_one, checklist_for, current_membership_for, mark_bypassed, mark_complete, mark_incomplete, requirement_document
from club.services.signup import place_signup_on_team, signup_queue
from club.services.signup_linking import link_to_existing_member
from management.forms import MemberOfficialEligibilityForm, MemberRefereeEligibilityForm, OnboardingRequirementForm, RequirementBypassForm, SignupLinkMemberForm, SignupTeamPlacementForm
from members.models import Member
from members.services.deletion import member_has_ties_outside_club
from members.services.photos import clear_player_photo, set_player_photo
from teams.models import OfficialProfile, RefereeProfile, Team, TeamMembership
from teams.services.eligibility import save_official_eligibility, save_referee_eligibility

router = Router(tags=["panel: onboarding & sign-up"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
UPLOAD_ERRORS = {413: ErrorOut, 415: ErrorOut, 422: ErrorOut}

MEMBERS = "/manage/members"
REQUIREMENTS = "/manage/onboarding-requirements"
SIGNUP = "/manage/signup"
DOCUMENT_TYPES = (*IMAGE_TYPES, *PDF_TYPES)
DOCUMENT_MAX_BYTES = 10 * 1024 * 1024


# --- helpers -----------------------------------------------------------------------------------


def _visible_member(request, club, member_id) -> Member:
    return get_object_or_404(members_visible_to(request.user, club, include_guardians=True), pk=member_id)


def _requirement(club, requirement_id) -> OnboardingRequirement:
    return get_object_or_404(OnboardingRequirement.objects.filter(club=club), pk=requirement_id)


def _requirement_out(requirement: OnboardingRequirement) -> dict:
    return {
        "id": requirement.pk,
        "name": requirement.name,
        "description": requirement.description,
        "requires_document": requirement.requires_document,
        "blocked_event_kinds": list(requirement.blocked_event_kinds or []),
        "is_active": requirement.is_active,
    }


def _item_out(requirement: OnboardingRequirement, status: MemberRequirementStatus | None) -> dict:
    state = "complete" if status and status.is_complete else "bypassed" if status and status.is_bypassed else "open"
    return {
        "requirement": _requirement_out(requirement),
        "state": state,
        "completed_at": status.completed_at if status else None,
        "completed_by": status.completed_by.email if status and status.completed_by_id else None,
        "note": status.note if status else "",
        "has_document": bool(status and status.document),
    }


def _checklist_out(membership: ClubMembership) -> dict:
    return {"member_id": membership.member_id, "season_id": membership.season_id, "items": [_item_out(requirement, status) for requirement, status in checklist_for(membership)]}


def _current_membership(club, member_id) -> ClubMembership:
    membership = current_membership_for(club, member_id)
    if membership is None:
        raise ApiError(404, "not_found", _("No current-season membership for this member."))
    return membership


def _queue_membership(club, member_id) -> ClubMembership:
    """The member's current-season MEMBER-kind membership (what the Sign-up actions work on); 404 without one."""
    season = current_season(club)
    if season is None:
        raise ApiError(404, "not_found", _("There is no active season."))
    return get_object_or_404(ClubMembership.objects.filter(club=club, season=season, kind=ClubMembership.Kind.MEMBER).select_related("member"), member_id=member_id)


# --- onboarding requirement settings -----------------------------------------------------------


@router.get(REQUIREMENTS, response={200: s.RequirementListOut, **ERRORS}, summary="List onboarding requirements")
def list_requirements(request):
    """What the club requires from every member after signing up (a photo, a medical certificate ...). Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    return {"items": [_requirement_out(requirement) for requirement in OnboardingRequirement.objects.filter(club=club)]}


def _requirement_form(club, values: dict, instance=None):
    """The web form over JSON values (booleans become checkboxes), (the form checks the per-club name uniqueness)."""
    data = {key: value for key, value in values.items() if key not in ("requires_document", "is_active") and value is not None}
    for flag in ("requires_document", "is_active"):
        if values.get(flag):
            data[flag] = "on"
    form = OnboardingRequirementForm(data, club=club, instance=instance)
    if not form.is_valid():
        raise form_error(form)
    return form


@router.post(REQUIREMENTS, response={201: s.RequirementOut, **ERRORS, **INVALID}, summary="Add an onboarding requirement")
def create_requirement(request, payload: s.RequirementIn):
    """Required for every member from now on (existing checklists show it as open). Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    form = _requirement_form(club, payload.model_dump())
    form.instance.club = club
    return 201, _requirement_out(form.save())


@router.get(REQUIREMENTS + "/{uuid:requirement_id}", response={200: s.RequirementOut, **ERRORS}, summary="One onboarding requirement")
def get_requirement(request, requirement_id: uuid.UUID):
    club = panel_club(request)
    require(request, club, can_manage_members)
    return _requirement_out(_requirement(club, requirement_id))


@router.patch(REQUIREMENTS + "/{uuid:requirement_id}", response={200: s.RequirementOut, **ERRORS, **INVALID}, summary="Change an onboarding requirement")
def update_requirement(request, requirement_id: uuid.UUID, payload: s.RequirementPatch):
    """Only the fields sent change. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    requirement = _requirement(club, requirement_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    form = _requirement_form(club, {**_requirement_out(requirement), **changes}, instance=requirement)
    return _requirement_out(form.save())


@router.delete(REQUIREMENTS + "/{uuid:requirement_id}", response={204: None, **ERRORS}, summary="Delete an onboarding requirement")
def delete_requirement(request, requirement_id: uuid.UUID):
    """It stops being required; the statuses recorded for it go with it. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    _requirement(club, requirement_id).delete()
    return 204, None


# --- a member's checklist ----------------------------------------------------------------------


@router.get(MEMBERS + "/{uuid:member_id}/requirements", response={200: s.ChecklistOut, **ERRORS}, summary="A member's onboarding checklist (current season)")
def member_checklist(request, member_id: uuid.UUID):
    """Every active requirement with its state. Staff who may see the member; 404 without a current-season membership."""
    club = panel_club(request)
    _visible_member(request, club, member_id)
    return _checklist_out(_current_membership(club, member_id))


def _item_for(membership, requirement) -> dict:
    status = MemberRequirementStatus.objects.filter(membership=membership, requirement=requirement).select_related("completed_by").first()
    return _item_out(requirement, status)


@router.post(MEMBERS + "/{uuid:member_id}/requirements/{uuid:requirement_id}/complete", response={200: s.ChecklistItemOut, **ERRORS, **UPLOAD_ERRORS}, summary="Mark a requirement complete")
def complete_requirement(request, member_id: uuid.UUID, requirement_id: uuid.UUID, note: Form[str] = "", document: File[UploadedFile] = None):
    """`multipart/form-data`: an optional `note` and, when the requirement asks for one, the `document` (image or PDF,
    stored privately). Clears an earlier bypass. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    membership = _current_membership(club, member_id)
    requirement = _requirement(club, requirement_id)
    upload = validate_upload(document, field="document", max_bytes=DOCUMENT_MAX_BYTES, content_types=DOCUMENT_TYPES) if document is not None else None
    mark_complete(membership, requirement, user=request.user, document=upload, note=note)
    return _item_for(membership, requirement)


@router.post(MEMBERS + "/{uuid:member_id}/requirements/{uuid:requirement_id}/bypass", response={200: s.ChecklistItemOut, **ERRORS, **INVALID}, summary="Mark a requirement as not needed")
def bypass_requirement(request, member_id: uuid.UUID, requirement_id: uuid.UUID, payload: s.BypassIn):
    """Stops the item blocking anything without a document having been received; the `note` (why) is required. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    membership = _current_membership(club, member_id)
    requirement = _requirement(club, requirement_id)
    form = RequirementBypassForm({"note": payload.note})
    if not form.is_valid():
        raise form_error(form)
    mark_bypassed(membership, requirement, user=request.user, note=form.cleaned_data["note"])
    return _item_for(membership, requirement)


@router.post(MEMBERS + "/{uuid:member_id}/requirements/{uuid:requirement_id}/incomplete", response={200: s.ChecklistItemOut, **ERRORS}, summary="Reopen a requirement")
def reopen_requirement(request, member_id: uuid.UUID, requirement_id: uuid.UUID):
    """Undoes a completion or bypass; an uploaded document is deleted. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    membership = _current_membership(club, member_id)
    requirement = _requirement(club, requirement_id)
    mark_incomplete(membership, requirement)
    return _item_for(membership, requirement)


@router.get(MEMBERS + "/{uuid:member_id}/requirements/{uuid:requirement_id}/document", response={200: None, **ERRORS}, summary="Download a requirement's document")
def requirement_document_file(request, member_id: uuid.UUID, requirement_id: uuid.UUID):
    """The uploaded file as an attachment, authenticated by the bearer token. Staff who may see the member; 404 otherwise."""
    club = panel_club(request)
    status = requirement_document(request.user, club, member_id, requirement_id)
    if status is None:
        raise ApiError(404, "not_found", _("No document has been uploaded for this requirement."))
    return FileResponse(status.document.open("rb"), as_attachment=True, filename=status.document.name.rsplit("/", 1)[-1])


# --- sign-up queue -----------------------------------------------------------------------------


def _row_out(membership: ClubMembership) -> dict:
    member = membership.member
    return {
        "member_id": member.pk,
        "first_name": member.first_name,
        "last_name": member.last_name,
        "status": membership.status,
        "fee_status": membership.fee_status,
        "signed_up_at": membership.signed_up_at,
        "created": membership.created,
        "onboarding_open": membership.onboarding_open,
        "is_clean": membership.is_clean,
        "is_oldest_pending": membership.is_oldest_pending,
        "blocked_event_kinds": sorted(membership.blocked_kinds),
        "checklist": [_item_out(requirement, status) for requirement, status in membership.checklist],
        "teams": [{"id": team.pk, "name": team.name} for team in membership.teams_registered],
        "requested_team_ids": sorted(membership.requested_team_ids, key=str),
        "possible_duplicates": [{"member_id": other.pk, "first_name": other.first_name, "last_name": other.last_name, "email": other.email} for other in membership.possible_duplicates],
    }


@router.get(SIGNUP, response={200: s.SignupQueueOut, **ERRORS}, summary="The sign-up queue")
def signup_list(request):
    """Current-season member sign-ups with something left to do: PENDING first (oldest first), then ACTIVE ones that
    are unpaid or have an open checklist item. Not paginated (a season's open queue, as the web screen). Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    season = current_season(club)
    return {
        "season_id": season.pk if season else None,
        "requirements_configured": OnboardingRequirement.objects.filter(club=club, is_active=True).exists(),
        "items": [_row_out(membership) for membership in signup_queue(club, season)],
    }


@router.post(SIGNUP + "/approve-all-clean", response={200: s.ApproveAllOut, **ERRORS, 409: ErrorOut}, summary="Approve every clean pending sign-up")
def signup_approve_all_clean(request):
    """Activates each PENDING membership that is paid (or waived) with its checklist resolved; `activated` may be 0.
    409 `no_active_season` without a current season. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    season = current_season(club)
    if season is None:
        raise ApiError(409, "no_active_season", _("There is no season covering today to approve sign-ups for."))
    return {"activated": approve_all_clean(club, season)}


@router.post(SIGNUP + "/{uuid:member_id}/approve", response={200: s.SignupApproveOut, **ERRORS, 409: ErrorOut}, summary="Approve one sign-up")
def signup_approve(request, member_id: uuid.UUID):
    """PENDING -> ACTIVE; 409 `not_ready` unless the sign-up is pending, paid (or waived) and fully checked off. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    membership = _queue_membership(club, member_id)
    if not approve_one(membership):
        raise ApiError(409, "not_ready", _("This sign-up isn't pending, or isn't both paid up and fully checked off yet."))
    return {"member_id": membership.member_id, "status": membership.status}


@router.post(SIGNUP + "/{uuid:member_id}/place-in-team", response={201: s.PlacementOut, **ERRORS, 409: ErrorOut, **INVALID}, summary="Roster a sign-up on a team")
def signup_place_in_team(request, member_id: uuid.UUID, payload: s.PlaceInTeamIn):
    """Adds them to the team's roster for the current season (even while still PENDING), with the jersey number requested at
    registration when it is still free. 409 `already_placed` when they are on that team already. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    membership = _queue_membership(club, member_id)
    season = current_season(club)
    # The form would run TeamMembership.clean() with no team for a foreign id and trip over it, so refuse that first.
    if not Team.objects.filter(club=club, pk=payload.team_id).exists():
        raise ApiError(422, "validation_error", fields={"team_id": [str(_("Select a valid choice. That choice is not one of the available choices."))]})
    form = SignupTeamPlacementForm({"team": str(payload.team_id)}, club=club, season=season, member=membership.member)
    if not form.is_valid():
        raise form_error(form, {"team": "team_id"})
    team = form.cleaned_data["team"]
    if TeamMembership.objects.filter(team=team, member=membership.member, season=season).exists():
        raise ApiError(409, "already_placed", _("This person is already on that team."))
    placed = place_signup_on_team(membership, team, season)
    return 201, {"member_id": membership.member_id, "team_id": team.pk, "team": team.name, "jersey_number": placed.jersey_number}


@router.post(SIGNUP + "/{uuid:member_id}/cancel", response={200: s.CancelOut, **ERRORS}, summary="Cancel a sign-up")
def signup_cancel(request, member_id: uuid.UUID):
    """Withdraws the registration (club.services.cancellation.cancel_membership: a brand-new person is deleted, a known
    one is soft-cancelled). Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    cancel_membership(_queue_membership(club, member_id))
    return {"result": "cancelled"}


@router.post(SIGNUP + "/{uuid:member_id}/link", response={200: s.LinkOut, **ERRORS, **INVALID}, summary="Link a sign-up to an existing member")
def signup_link(request, member_id: uuid.UUID, payload: s.LinkIn):
    """Fixes an accidental duplicate: the registration moves onto `target_member_id`. 422 on `target_member_id` for an
    unknown member or one that already has a registration this season. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    membership = _queue_membership(club, member_id)
    form = SignupLinkMemberForm({"member": str(payload.target_member_id)}, club=club, exclude_member=membership.member)
    if not form.is_valid():
        raise form_error(form, {"member": "target_member_id"})
    target = form.cleaned_data["member"]
    try:
        link_to_existing_member(membership, target)
    except ValidationError as error:
        raise ApiError(422, "validation_error", fields={"target_member_id": [str(message) for message in error.messages]}) from None
    return {"member_id": target.pk}


# --- eligibility -------------------------------------------------------------------------------


def _officials_enabled(club) -> None:
    if not club_features(club).get("officials"):
        raise ApiError(404, "not_found", _("Officials are not enabled for this club."))


def _referee_out(profile: RefereeProfile | None) -> dict:
    if profile is None:
        return {"level_id": None, "valid_until": None, "excluded_team_ids": [], "eligible_team_ids": []}
    return {
        "level_id": profile.level_id,
        "valid_until": profile.valid_until,
        "excluded_team_ids": sorted((team.pk for team in profile.excluded_teams.all()), key=str),
        "eligible_team_ids": sorted(profile.eligible_team_ids(), key=str),
    }


@router.get(MEMBERS + "/{uuid:member_id}/referee-eligibility", response={200: s.RefereeEligibilityOut, **ERRORS}, summary="A member's referee eligibility")
def get_referee_eligibility(request, member_id: uuid.UUID):
    """Level, validity and the teams excluded for this person. No profile = no level. Staff who may see the member."""
    club = panel_club(request)
    member = _visible_member(request, club, member_id)
    return _referee_out(RefereeProfile.objects.filter(club=club, member=member).select_related("level").first())


@router.put(MEMBERS + "/{uuid:member_id}/referee-eligibility", response={200: s.RefereeEligibilityOut, **ERRORS, **INVALID}, summary="Set a member's referee eligibility")
def put_referee_eligibility(request, member_id: uuid.UUID, payload: s.RefereeEligibilityIn):
    """Replaces the profile (created when missing); open invites for games they may no longer referee are withdrawn.
    `level_id: null` makes them ineligible. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    member = _visible_member(request, club, member_id)
    data = {"level": str(payload.level_id) if payload.level_id else "", "valid_until": payload.valid_until.isoformat() if payload.valid_until else "", "excluded_teams": [str(pk) for pk in payload.excluded_team_ids]}
    form = MemberRefereeEligibilityForm(data, club=club, member=member)
    if not form.is_valid():
        raise form_error(form, {"level": "level_id", "excluded_teams": "excluded_team_ids"})
    return _referee_out(save_referee_eligibility(form))


def _official_out(profile: OfficialProfile | None) -> dict:
    return {"level_id": profile.level_id if profile else None, "valid_until": profile.valid_until if profile else None}


@router.get(MEMBERS + "/{uuid:member_id}/official-eligibility", response={200: s.OfficialEligibilityOut, **ERRORS}, summary="A member's official eligibility")
def get_official_eligibility(request, member_id: uuid.UUID):
    """Needs the club's `officials` feature (404 otherwise). Staff who may see the member."""
    club = panel_club(request)
    _officials_enabled(club)
    member = _visible_member(request, club, member_id)
    return _official_out(OfficialProfile.objects.filter(club=club, member=member).first())


@router.put(MEMBERS + "/{uuid:member_id}/official-eligibility", response={200: s.OfficialEligibilityOut, **ERRORS, **INVALID}, summary="Set a member's official eligibility")
def put_official_eligibility(request, member_id: uuid.UUID, payload: s.OfficialEligibilityIn):
    """Replaces the profile (created when missing). Needs the `officials` feature (404 otherwise). Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    _officials_enabled(club)
    member = _visible_member(request, club, member_id)
    data = {"level": str(payload.level_id) if payload.level_id else "", "valid_until": payload.valid_until.isoformat() if payload.valid_until else ""}
    form = MemberOfficialEligibilityForm(data, club=club, member=member)
    if not form.is_valid():
        raise form_error(form, {"level": "level_id"})
    return _official_out(save_official_eligibility(form))


# --- player photo ------------------------------------------------------------------------------


def _photo_member(request, club, member_id) -> Member:
    require(request, club, can_manage_members)
    member = _visible_member(request, club, member_id)
    if member_has_ties_outside_club(member, club):
        raise ApiError(409, "person_locked", _("This person's details belong to their own account or another club and can't be changed here."))
    return member


@router.post(MEMBERS + "/{uuid:member_id}/photo", response={200: member_schemas.MemberDetailOut, **ERRORS, 409: ErrorOut, **UPLOAD_ERRORS}, summary="Set a member's player photo")
def set_photo(request, member_id: uuid.UUID, photo: File[UploadedFile]):
    """`multipart/form-data` with the image as `photo` (JPEG, PNG, WebP or GIF, at most 10 MB; the bytes must really be an
    image). The official player picture, stored privately. 409 `person_locked` for a record shared with an account or
    another club. Member admins."""
    club = panel_club(request)
    member = _photo_member(request, club, member_id)
    upload = validate_upload(photo, field="photo", content_types=IMAGE_TYPES)
    try:
        set_player_photo(member, upload)
    except ValidationError as error:
        raise ApiError(422, "validation_error", fields={"photo": [str(message) for message in error.messages]}) from None
    member.refresh_from_db()
    return member_detail(request, club, member)


@router.delete(MEMBERS + "/{uuid:member_id}/photo", response={200: member_schemas.MemberDetailOut, **ERRORS, 409: ErrorOut}, summary="Remove a member's player photo")
def delete_photo(request, member_id: uuid.UUID):
    """Idempotent: without a photo nothing changes. Member admins."""
    club = panel_club(request)
    member = _photo_member(request, club, member_id)
    clear_player_photo(member)
    member.refresh_from_db()
    return member_detail(request, club, member)
