"""Season memberships, fees and onboarding. Everything goes through ``members.services``.

Access: fee, payment, onboarding and history endpoints are admin-only (``access.can_manage_members``). A member may
read only the status of their own membership; any membership someone cannot see is a 404, never a 403, so its
existence does not leak.
"""

from uuid import UUID

from django.db.models import Count, Exists, OuterRef, Q, Value
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from ninja import File, Form, Router, UploadedFile
from ninja.pagination import paginate

from events.models import Season
from members.models import ClubMembership, FeePayment, Member, MemberRequirementStatus, OnboardingRequirement
from members.services import access, audit, onboarding
from members.services import memberships as fees
from members.services.errors import NotAllowed
from teams.models import TeamMembership

from ..schemas_memberships import (
    AuditEntryOut,
    BypassIn,
    ChecklistItemOut,
    MarkPaidIn,
    MembershipCreateIn,
    MembershipOut,
    MembershipOwnOut,
    MembershipPatchIn,
    PaymentIn,
    PaymentOut,
    RequirementIn,
    RequirementOut,
    RequirementPatchIn,
    SeasonOut,
    SummaryOut,
    WaiveIn,
)

router = Router()
Fee = ClubMembership.FeeStatus


# --- helpers ------------------------------------------------------------------------------------------------------


def _admin(request) -> None:
    if not access.can_manage_members(request.user):
        raise NotAllowed("You are not allowed to manage members.")


def _club_user(request) -> None:
    from django.core.exceptions import ObjectDoesNotExist, PermissionDenied

    try:
        _ = request.user.tenant_perms
    except ObjectDoesNotExist:
        raise PermissionDenied from None


def _queryset():
    """Memberships with the list columns computed in the database / prefetched: no per-row queries."""
    active = OnboardingRequirement.objects.filter(is_active=True).count()
    resolved = Count(
        "requirement_statuses",
        filter=Q(requirement_statuses__requirement__is_active=True) & (Q(requirement_statuses__is_complete=True) | Q(requirement_statuses__is_bypassed=True)),
        distinct=True,
    )
    return ClubMembership.objects.select_related("member", "season").prefetch_related("payments").annotate(open_count=Value(active) - resolved)


def _fresh(pk) -> ClubMembership:
    return _queryset().get(pk=pk)


def _membership(request, membership_id, *, admin_only: bool = True) -> ClubMembership:
    """The membership, or 404 when the caller may not see it. ``admin_only`` endpoints answer 403 to the owner."""
    membership = get_object_or_404(ClubMembership.objects.select_related("member", "season"), pk=membership_id)
    if access.can_manage_members(request.user):
        return membership
    if membership.member.user_id is not None and membership.member.user_id == request.user.pk:
        if admin_only:
            raise NotAllowed("You are not allowed to manage members.")
        return membership
    raise Http404


def _season_out(season, current=None) -> dict:
    return {"id": season.pk, "name": season.name, "start_date": season.start_date, "end_date": season.end_date, "is_current": current is not None and season.pk == current.pk}


def _requirement(requirement_id) -> OnboardingRequirement:
    return get_object_or_404(OnboardingRequirement, pk=requirement_id)


def _season_param(season: UUID | None) -> Season | None:
    return get_object_or_404(Season, pk=season) if season else Season.get_current()


# --- seasons, list, summary -----------------------------------------------------------------------------------------


@router.get("/seasons", response=list[SeasonOut], summary="Seasons, newest first (for pickers)")
def seasons(request):
    """Any authenticated club user. ``is_current`` marks the season covering today."""
    _club_user(request)
    current = Season.get_current()
    return [_season_out(s, current) for s in Season.objects.order_by("-start_date")]


@router.get("", response=list[MembershipOut], summary="List memberships (admin)")
@paginate
def list_memberships(request, season: UUID | None = None, status: str | None = None, fee_status: str | None = None, team: UUID | None = None, q: str | None = None):
    """Admin only. Filters: ``season`` (default: the current season), ``status``, ``fee_status`` (a fee status, or
    ``not_paid`` = unpaid + partially paid, cancelled memberships excluded), ``team`` (on that team's roster this
    season) and ``q`` (member name, email or license number). Paginated with limit/offset."""
    _admin(request)
    chosen = _season_param(season)
    qs = _queryset().filter(season=chosen) if chosen else ClubMembership.objects.none()
    if status:
        qs = qs.filter(status=status)
    if fee_status == "not_paid":
        qs = qs.filter(fee_status__in=[Fee.UNPAID, Fee.PARTIALLY_PAID]).exclude(status=ClubMembership.StatusChoices.CANCELLED)
    elif fee_status:
        qs = qs.filter(fee_status=fee_status)
    if team:
        qs = qs.filter(Exists(TeamMembership.objects.filter(team_id=team, member=OuterRef("member"), season=OuterRef("season"))))
    if q:
        for word in q.split():
            qs = qs.filter(Q(member__first_name__icontains=word) | Q(member__last_name__icontains=word) | Q(member__email__icontains=word) | Q(member__license_number__icontains=word))
    return qs


@router.get("/summary", response=SummaryOut, summary="Fee KPIs for a season (admin)")
def summary(request, season: UUID | None = None):
    """Admin only. Counts memberships of the season (default: current) by fee status, cancelled ones excluded.
    ``paid_rate`` = paid / (total - waived), 0 when nothing is payable."""
    _admin(request)
    chosen = _season_param(season)
    counts = {"total": 0, "paid": 0, "partially_paid": 0, "unpaid": 0, "waived": 0}
    if chosen:
        qs = ClubMembership.objects.filter(season=chosen).exclude(status=ClubMembership.StatusChoices.CANCELLED)
        counts = qs.aggregate(
            total=Count("pk"),
            paid=Count("pk", filter=Q(fee_status=Fee.PAID)),
            partially_paid=Count("pk", filter=Q(fee_status=Fee.PARTIALLY_PAID)),
            unpaid=Count("pk", filter=Q(fee_status=Fee.UNPAID)),
            waived=Count("pk", filter=Q(fee_status=Fee.WAIVED)),
        )
    payable = counts["total"] - counts["waived"]
    rate = round(counts["paid"] / payable, 4) if payable else 0.0
    return {**counts, "paid_rate": rate, "season": _season_out(chosen, Season.get_current()) if chosen else None}


@router.post("", response={201: MembershipOut}, summary="Sign a member up for a season (admin)")
def create_membership(request, payload: MembershipCreateIn):
    """Admin only. One membership per member and season (422 otherwise). Status may be pending, lapsed or
    cancelled; use ``activate`` to make it active."""
    _admin(request)
    member = get_object_or_404(Member, pk=payload.member_id)
    chosen = get_object_or_404(Season, pk=payload.season_id)
    data = payload.dict(exclude={"member_id", "season_id"})
    membership = fees.create_membership(request.user, member, chosen, **data)
    return 201, _fresh(membership.pk)


# --- requirements (static paths before /{id}) -----------------------------------------------------------------------


@router.get("/requirements", response=list[RequirementOut], summary="Onboarding requirements (admin)")
def list_requirements(request):
    """Admin only. All requirements, inactive ones included, by name."""
    _admin(request)
    return OnboardingRequirement.objects.order_by("name")


@router.post("/requirements", response={201: RequirementOut}, summary="Define an onboarding requirement (admin)")
def create_requirement(request, payload: RequirementIn):
    """Admin only. Names are unique (case-insensitive); ``blocked_event_kinds`` must be event kinds (422)."""
    _admin(request)
    return 201, onboarding.create_requirement(request.user, **payload.dict())


@router.patch("/requirements/{uuid:requirement_id}", response=RequirementOut, summary="Edit a requirement (admin)")
def update_requirement(request, requirement_id: UUID, payload: RequirementPatchIn):
    """Admin only. Only the fields sent are changed."""
    _admin(request)
    return onboarding.update_requirement(request.user, _requirement(requirement_id), **payload.dict(exclude_unset=True))


@router.delete("/requirements/{uuid:requirement_id}", response={204: None}, summary="Delete a requirement (admin)")
def delete_requirement(request, requirement_id: UUID):
    """Admin only. Also removes every membership's status for it; prefer ``is_active=false`` to keep history."""
    _admin(request)
    onboarding.delete_requirement(request.user, _requirement(requirement_id))
    return 204, None


@router.delete("/payments/{uuid:payment_id}", response=MembershipOut, summary="Delete a mistaken payment (admin)")
def delete_payment(request, payment_id: UUID):
    """Admin only. Removes the payment, re-derives the fee status and returns the membership."""
    _admin(request)
    payment = get_object_or_404(FeePayment.objects.select_related("membership__member", "membership__season"), pk=payment_id)
    membership = fees.delete_payment(request.user, payment)
    return _fresh(membership.pk)


# --- one membership -------------------------------------------------------------------------------------------------


@router.get("/{uuid:membership_id}", response=MembershipOut | MembershipOwnOut, summary="One membership")
def get_membership(request, membership_id: UUID):
    """Admin: the full membership with fee data. A member: only the status of their own membership (no fee
    data). Anyone else: 404."""
    membership = _membership(request, membership_id, admin_only=False)
    if access.can_manage_members(request.user):
        return _fresh(membership.pk)
    return {"scope": "self", "id": membership.pk, "season": _season_out(membership.season, Season.get_current()), "status": membership.status, "activated_at": membership.activated_at}


@router.patch("/{uuid:membership_id}", response=MembershipOut, summary="Update status / fee settings (admin)")
def update_membership(request, membership_id: UUID, payload: MembershipPatchIn):
    """Admin only. Status (not ``active``; use activate) and fee settings; ``amount_paid`` and ``fee_status`` are
    derived from payments and cannot be set. Only the fields sent are changed."""
    membership = _membership(request, membership_id)
    fees.update_membership(request.user, membership, **payload.dict(exclude_unset=True))
    return _fresh(membership.pk)


@router.delete("/{uuid:membership_id}", response={204: None}, summary="Delete a pending, unpaid membership (admin)")
def delete_membership(request, membership_id: UUID):
    """Admin only. 422 unless the membership is pending and unpaid (cancel it otherwise). Audited on the member."""
    membership = _membership(request, membership_id)
    fees.delete_membership(request.user, membership)
    return 204, None


@router.post("/{uuid:membership_id}/activate", response=MembershipOut, summary="Activate (admin)")
def activate(request, membership_id: UUID):
    """Admin only. 422 unless the fee is paid or waived and every active onboarding requirement is completed or
    bypassed; a cancelled membership cannot be activated."""
    membership = _membership(request, membership_id)
    fees.activate(request.user, membership)
    return _fresh(membership.pk)


@router.post("/{uuid:membership_id}/cancel", response=MembershipOut, summary="Cancel (admin)")
def cancel(request, membership_id: UUID):
    """Admin only. Payments are kept."""
    membership = _membership(request, membership_id)
    fees.cancel(request.user, membership)
    return _fresh(membership.pk)


@router.post("/{uuid:membership_id}/waive", response=MembershipOut, summary="Waive or un-waive the fee (admin)")
def waive(request, membership_id: UUID, payload: WaiveIn):
    """Admin only. ``waived=true`` waives the fee (no payments can be recorded then); ``false`` lifts the waiver and
    the fee status is re-derived from the payments."""
    membership = _membership(request, membership_id)
    fees.set_fee_waived(request.user, membership, payload.waived)
    return _fresh(membership.pk)


# --- payments -------------------------------------------------------------------------------------------------------


@router.get("/{uuid:membership_id}/payments", response=list[PaymentOut], summary="Payments (admin)")
def list_payments(request, membership_id: UUID):
    """Admin only. Payments of the membership, newest first."""
    membership = _membership(request, membership_id)
    return membership.payments.select_related("recorded_by").order_by("-paid_at", "-created")


@router.post("/{uuid:membership_id}/payments", response={201: PaymentOut}, summary="Record a payment (admin)")
def add_payment(request, membership_id: UUID, payload: PaymentIn):
    """Admin only. Amount must be positive (422); 422 when the fee is waived. Overpaying is allowed: the remaining
    balance never goes below zero. The fee status is re-derived."""
    membership = _membership(request, membership_id)
    payment = fees.record_payment(request.user, membership, **payload.dict())
    return 201, payment


@router.post("/{uuid:membership_id}/mark-paid", response={201: PaymentOut}, summary="Pay the remaining balance (admin)")
def mark_paid(request, membership_id: UUID, payload: MarkPaidIn | None = None):
    """Admin only. Records one payment for the whole remaining balance; 422 when nothing is left or the fee is waived."""
    membership = _membership(request, membership_id)
    return 201, fees.mark_paid(request.user, membership, **(payload.dict() if payload else {}))


# --- onboarding checklist -------------------------------------------------------------------------------------------


@router.get("/{uuid:membership_id}/checklist", response=list[ChecklistItemOut], summary="Onboarding checklist (admin)")
def checklist(request, membership_id: UUID):
    """Admin only. One item per active requirement with its state (open, complete or bypassed)."""
    membership = _membership(request, membership_id)
    return onboarding.checklist_for(membership)


def _item(membership, requirement):
    return next(i for i in onboarding.checklist_for(membership) if i.requirement.pk == requirement.pk)


@router.post("/{uuid:membership_id}/checklist/{uuid:requirement_id}/complete", response=ChecklistItemOut, summary="Mark a requirement received (admin)")
def complete(request, membership_id: UUID, requirement_id: UUID, note: Form[str] = "", document: File[UploadedFile] = None):
    """Admin only. Multipart: optional ``document`` file and ``note``. A requirement that needs a document and has
    none is 422. The file is stored privately (see the document endpoint)."""
    membership = _membership(request, membership_id)
    requirement = _requirement(requirement_id)
    onboarding.mark_complete(request.user, membership, requirement, document=document, note=note)
    return _item(membership, requirement)


@router.post("/{uuid:membership_id}/checklist/{uuid:requirement_id}/bypass", response=ChecklistItemOut, summary="Mark a requirement not needed (admin)")
def bypass(request, membership_id: UUID, requirement_id: UUID, payload: BypassIn):
    """Admin only. Resolves the requirement for this person without proof."""
    membership = _membership(request, membership_id)
    requirement = _requirement(requirement_id)
    onboarding.bypass(request.user, membership, requirement, note=payload.note)
    return _item(membership, requirement)


@router.post("/{uuid:membership_id}/checklist/{uuid:requirement_id}/reopen", response=ChecklistItemOut, summary="Undo a completion or bypass (admin)")
def reopen(request, membership_id: UUID, requirement_id: UUID):
    """Admin only. 422 when not resolved. Deletes the attached document."""
    membership = _membership(request, membership_id)
    requirement = _requirement(requirement_id)
    onboarding.reopen(request.user, membership, requirement)
    return _item(membership, requirement)


@router.get("/{uuid:membership_id}/checklist/{uuid:requirement_id}/document", response={200: None}, summary="Download the attached document (admin)")
def document(request, membership_id: UUID, requirement_id: UUID):
    """Admin only. Streams the private file through this authenticated endpoint (no storage URL is ever exposed);
    404 when the requirement has no document for this membership."""
    membership = _membership(request, membership_id)
    status = MemberRequirementStatus.objects.filter(membership=membership, requirement_id=requirement_id).first()
    if status is None or not status.document:
        raise Http404
    response = FileResponse(status.document.open("rb"), as_attachment=True, filename=status.document.name.rsplit("/", 1)[-1])
    response["Cache-Control"] = "private, no-store"
    return response


# --- history --------------------------------------------------------------------------------------------------------


@router.get("/{uuid:membership_id}/history", response=list[AuditEntryOut], summary="Audit trail of this membership (admin)")
@paginate
def history(request, membership_id: UUID):
    """Admin only, paginated, newest first. Audit entries whose target is this membership, one of its payments
    (deleted ones included) or one of its requirement statuses."""
    membership = _membership(request, membership_id)
    payment_ids = [str(pk) for pk in membership.payments.values_list("pk", flat=True)]
    status_ids = [str(pk) for pk in membership.requirement_statuses.values_list("pk", flat=True)]
    deleted_payment_label = f"{membership.season} - {membership.member}:"
    scope = (
        Q(target_type="clubmembership", target_id=str(membership.pk))
        | Q(target_type="feepayment", target_id__in=payment_ids)
        | Q(target_type="feepayment", target_label__startswith=deleted_payment_label)
        | Q(target_type="memberrequirementstatus", target_id__in=status_ids)
    )
    return audit.history(membership.member).filter(scope)
