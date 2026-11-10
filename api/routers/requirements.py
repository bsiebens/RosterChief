"""Requirements (``/requirements``): what a club asks of its members -- documents (a medical certificate, a photo ...) or questions (extra information) -- each
member's checklist per season, and the actions on it. Managing requirements and completing / bypassing / reopening / downloading
needs ``members.manage_members`` (403, the download answers 404 instead); reading a member's checklist needs the right to see
that member. Rules live in ``members.services.onboarding``; the daily reminders in ``members.services.requirement_reminders``."""

import datetime
from uuid import UUID

from django.db.models import Count
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from ninja import File, Form, Router, UploadedFile

from api.auth import AUTHENTICATORS
from api.schemas_requirements import AnswerIn, BypassIn, ChecklistOut, ItemOut, RequirementIn, RequirementListItem, RequirementOut, RequirementPatch
from events.models import Season
from members.models import ClubMembership, Member, MemberRequirementStatus, OnboardingRequirement
from members.services import access, onboarding
from members.services.errors import Invalid

router = Router(auth=AUTHENTICATORS)


def _requirement(requirement_id) -> OnboardingRequirement:
    return get_object_or_404(OnboardingRequirement, pk=requirement_id)


def _membership(membership_id) -> ClubMembership:
    return get_object_or_404(ClubMembership.objects.select_related("member", "season"), pk=membership_id)


def _item(membership, requirement) -> onboarding.ChecklistItem:
    return next(i for i in onboarding.checklist_for(membership) if i.requirement.pk == requirement.pk)


# --- requirements ----------------------------------------------------------------------------------------------------


@router.get("", response=list[RequirementListItem], summary="All requirements, inactive ones included", operation_id="list_requirements")
def list_requirements(request):
    access.require_admin(request.user)
    return OnboardingRequirement.objects.annotate(usage_count=Count("statuses")).order_by("name")


@router.post("", response={201: RequirementOut}, summary="Define a requirement", operation_id="create_requirement")
def create_requirement(request, payload: RequirementIn):
    """Names are unique (any case). ``blocked_event_kinds`` must be event kinds; ``valid_for_months`` 1-120 or null."""
    return 201, onboarding.create_requirement(request.user, **payload.dict())


@router.patch("/{uuid:requirement_id}", response=RequirementOut, summary="Edit a requirement", operation_id="update_requirement")
def update_requirement(request, requirement_id: UUID, payload: RequirementPatch):
    return onboarding.update_requirement(request.user, _requirement(requirement_id), **payload.dict(exclude_unset=True))


@router.delete("/{uuid:requirement_id}", response={204: None}, summary="Delete a requirement and every status for it", operation_id="delete_requirement")
def delete_requirement(request, requirement_id: UUID):
    """Prefer switching it off (``is_active=false``) to keep the history."""
    onboarding.delete_requirement(request.user, _requirement(requirement_id))
    return 204, None


# --- a member's checklist --------------------------------------------------------------------------------------------


@router.get("/members/{uuid:member_id}/checklist", response=ChecklistOut, summary="A member's documents for a season", operation_id="member_checklist")
def member_checklist(request, member_id: UUID, season_id: UUID | None = None):
    """The season's membership (default: the current season, else the most recent one) with every active requirement:
    ``display`` is missing / complete / expiring_soon / expired / bypassed. 404 when the caller may not see the member."""
    member = Member.objects.filter(pk=member_id).first()
    if member is None or not access.can_view_member(request.user, member):
        raise Http404
    memberships = ClubMembership.objects.filter(member=member).select_related("season")
    if season_id is not None:
        membership = get_object_or_404(memberships, season_id=season_id)
    else:
        current = Season.get_current()
        membership = (memberships.filter(season=current).first() if current else None) or memberships.order_by("-season__start_date").first()
    if membership is None:
        return {"membership": None, "items": [], "total": 0, "done": 0, "open": 0, "expiring": 0, "expired": 0}
    items = onboarding.checklist_for(membership)
    return {
        "membership": {"id": membership.pk, "season": {"id": membership.season_id, "name": membership.season.name}, "status": membership.status, "fee_status": membership.fee_status},
        "items": items,
        "total": len(items),
        "done": sum(1 for i in items if i.resolved),
        "open": sum(1 for i in items if not i.resolved),
        "expiring": sum(1 for i in items if i.display == "expiring_soon"),
        "expired": sum(1 for i in items if i.display == "expired"),
    }


# --- actions on one membership's item --------------------------------------------------------------------------------


@router.post("/memberships/{uuid:membership_id}/{uuid:requirement_id}/complete", response=ItemOut, summary="Mark a requirement received", operation_id="complete_requirement")
def complete(request, membership_id: UUID, requirement_id: UUID, note: Form[str] = "", valid_until: Form[datetime.date | None] = None, document: File[UploadedFile] = None):
    """Multipart: optional ``document`` (documents and pictures, up to 10 MB), ``note`` and ``valid_until`` (default: the
    requirement's validity from today). Completing again updates the item. A requirement that needs a document and has none is 422."""
    membership, requirement = _membership(membership_id), _requirement(requirement_id)
    onboarding.mark_complete(request.user, membership, requirement, document=document, note=note, valid_until=valid_until)
    return _item(membership, requirement)


@router.post("/memberships/{uuid:membership_id}/{uuid:requirement_id}/answer", response=ItemOut, summary="Answer a question for this person", operation_id="answer_requirement")
def answer(request, membership_id: UUID, requirement_id: UUID, payload: AnswerIn):
    """For a requirement of kind ``question``: ``answer`` is validated for its type (a number within the range, a choice among the options ...)
    and completes the item; answering again replaces the answer. 422 keyed ``answer``; 422 ``not_a_question`` for a document."""
    membership, requirement = _membership(membership_id), _requirement(requirement_id)
    onboarding.answer(request.user, membership, requirement, payload.answer, note=payload.note.strip(), valid_until=payload.valid_until)
    return _item(membership, requirement)


@router.post("/memberships/{uuid:membership_id}/{uuid:requirement_id}/bypass", response=ItemOut, summary="Mark a requirement not needed for this person", operation_id="bypass_requirement")
def bypass(request, membership_id: UUID, requirement_id: UUID, payload: BypassIn):
    """The note (why it is not needed) is required."""
    if not payload.note.strip():
        access.require_admin(request.user)
        raise Invalid("Say why this is not needed.", {"note": ["This field is required."]})
    membership, requirement = _membership(membership_id), _requirement(requirement_id)
    onboarding.bypass(request.user, membership, requirement, note=payload.note.strip())
    return _item(membership, requirement)


@router.post("/memberships/{uuid:membership_id}/{uuid:requirement_id}/reopen", response=ItemOut, summary="Undo a completion or bypass (removes the document)", operation_id="reopen_requirement")
def reopen(request, membership_id: UUID, requirement_id: UUID):
    membership, requirement = _membership(membership_id), _requirement(requirement_id)
    onboarding.reopen(request.user, membership, requirement)
    return _item(membership, requirement)


@router.get("/memberships/{uuid:membership_id}/{uuid:requirement_id}/document", response={200: None}, summary="Download the attached document", operation_id="download_requirement_document")
def document(request, membership_id: UUID, requirement_id: UUID):
    """Streams the private file through this authenticated endpoint. 404 (never 403) when the caller may not manage members
    or there is no document."""
    if not access.can_manage_members(request.user):
        raise Http404
    status = MemberRequirementStatus.objects.filter(membership_id=membership_id, requirement_id=requirement_id).first()
    if status is None or not status.document:
        raise Http404
    response = FileResponse(status.document.open("rb"), as_attachment=True, filename=status.document.name.rsplit("/", 1)[-1])
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
