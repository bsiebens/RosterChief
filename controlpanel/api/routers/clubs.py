"""Clubs: lifecycle (create, edit, archive, restore, delete) and their administrators."""

from uuid import UUID

from django.contrib.auth import get_user_model
from django.db.models import OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from ninja import Query, Router, Schema
from ninja.pagination import paginate

from authorization.permissions import is_platform_superuser  # noqa: F401
from authorization.services import ProtectedRoleError
from club import services as club_services
from club.models import Club, Domain, Sport
from controlpanel import audit
from controlpanel.models import ClubProvisioning
from controlpanel.services import provisioning
from features.models import Subscription

from ..auth import superuser_auth
from ..errors import ApiError
from ..idempotency import idempotent
from ..schemas import ClubAdminIn, ClubAdminOut, ClubCreateIn, ClubDeleteIn, ClubDetailOut, ClubListOut, ClubUpdateIn, ProvisioningOut

router = Router(tags=["clubs"])


def _clubs():
    current_plan = Subscription.objects.filter(club=OuterRef("pk"), ends_at__isnull=True).values("plan__code")[:1]
    return Club.objects.exclude(schema_name="public").select_related("sport", "owner").annotate(plan_code=Subquery(current_plan))


def _get_club(club_id: UUID) -> Club:
    return get_object_or_404(_clubs(), pk=club_id)


def _domains(club):
    return list(Domain.objects.filter(tenant=club).order_by("-is_primary", "domain").values_list("domain", flat=True))


class ClubFilters(Schema):
    q: str | None = None
    status: str = "active"  # active | archived | all
    sport_id: UUID | None = None


@router.get("/clubs", response=list[ClubListOut], summary="Clubs, with search and an active/archived filter")
@paginate
def list_clubs(request, filters: ClubFilters = Query(...)):
    qs = _clubs().order_by("name")
    if filters.status == "active":
        qs = qs.filter(archived_at__isnull=True)
    elif filters.status == "archived":
        qs = qs.filter(archived_at__isnull=False)
    elif filters.status != "all":
        raise ApiError(422, "validation_error", "status must be active, archived or all.", {"status": ["Use active, archived or all."]})
    if filters.q:
        qs = qs.filter(Q(name__icontains=filters.q) | Q(slug__icontains=filters.q))
    if filters.sport_id:
        qs = qs.filter(sport_id=filters.sport_id)
    return [ClubListOut.of(c) for c in qs]


@router.post("/clubs", response={202: ProvisioningOut}, summary="Request a new club. Accepted at once; the schema is built in the background (poll the returned id)")
@idempotent
def create_club(request, payload: ClubCreateIn):
    sport = get_object_or_404(Sport, pk=payload.sport_id) if payload.sport_id else None
    try:
        owner, owner_created = club_services.resolve_account(payload.owner_email, first_name=payload.owner_first_name, last_name=payload.owner_last_name)
    except club_services.UnverifiedEmailError as e:
        raise ApiError(409, "unverified_email", str(e)) from e
    except club_services.AccountError as e:
        raise ApiError(422, "validation_error", str(e), {"owner_first_name": [str(e)]}) from e
    extra = payload.dict(exclude={"name", "sport_id", "owner_email", "owner_first_name", "owner_last_name"}, exclude_none=True)
    job = provisioning.request_club(name=payload.name, owner=owner, sport=sport, club_fields=extra, requested_by=request.user)
    if owner_created:
        club_services.send_password_setup_email(request, owner)
    job.refresh_from_db()  # with an eager (test) worker the build has already finished
    return 202, ProvisioningOut.of(job)


@router.get("/club-provisioning", response=list[ProvisioningOut], summary="Club creation requests, newest first")
@paginate
def list_provisioning(request, status: str | None = None):
    qs = ClubProvisioning.objects.select_related("club")
    if status:
        qs = qs.filter(status=status)
    return [ProvisioningOut.of(j) for j in qs]


@router.get("/club-provisioning/{job_id}", response=ProvisioningOut, summary="Progress of a club creation request")
def get_provisioning(request, job_id: UUID):
    return ProvisioningOut.of(get_object_or_404(ClubProvisioning.objects.select_related("club"), pk=job_id))


@router.get("/clubs/{club_id}", response=ClubDetailOut, summary="One club with its domains and administrators")
def get_club(request, club_id: UUID):
    club = _get_club(club_id)
    return ClubDetailOut.of(club, _domains(club), club_services.club_admins(club))


@router.patch("/clubs/{club_id}", response=ClubDetailOut, summary="Edit a club (slug and schema never change)")
def update_club(request, club_id: UUID, payload: ClubUpdateIn):
    club = _get_club(club_id)
    changes = payload.dict(exclude_unset=True)
    if "sport_id" in changes:
        club.sport = get_object_or_404(Sport, pk=changes.pop("sport_id")) if payload.sport_id else None
    for field, value in changes.items():
        setattr(club, field, value)
    club.full_clean(exclude=["owner", "slug", "schema_name", "logo", "event_background"])
    club.save()
    audit.record(request.user, "club.updated", target=club, club=club, data={"fields": sorted(payload.dict(exclude_unset=True))})
    return ClubDetailOut.of(_get_club(club_id), _domains(club), club_services.club_admins(club))


@router.post("/clubs/{club_id}/archive", response=ClubDetailOut, summary="Archive: the subdomain stops resolving, data is kept")
def archive_club(request, club_id: UUID):
    club = _get_club(club_id)
    club_services.archive_club(club)
    audit.record(request.user, "club.archived", target=club, club=club)
    return ClubDetailOut.of(_get_club(club_id), _domains(club), club_services.club_admins(club))


@router.post("/clubs/{club_id}/restore", response=ClubDetailOut, summary="Restore an archived club")
def restore_club(request, club_id: UUID):
    club = _get_club(club_id)
    club_services.restore_club(club)
    audit.record(request.user, "club.restored", target=club, club=club)
    return ClubDetailOut.of(_get_club(club_id), _domains(club), club_services.club_admins(club))


@router.delete("/clubs/{club_id}", response={204: None}, auth=superuser_auth, summary="Permanently delete a club and its schema (superuser; body must repeat the slug)")
def delete_club(request, club_id: UUID, payload: ClubDeleteIn):
    club = _get_club(club_id)
    if payload.confirm_slug != club.slug:
        raise ApiError(422, "confirmation_mismatch", "confirm_slug does not match the club's slug.", {"confirm_slug": ["Does not match."]})
    summary = {"name": club.name, "slug": club.slug, "schema": club.schema_name}
    audit.record(request.user, "club.deleted", target_type="club", target_id=club.pk, data=summary)
    club_services.delete_club(club)
    return 204, None


@router.get("/clubs/{club_id}/admins", response=list[ClubAdminOut], summary="Administrators (Owner role) of a club")
def list_club_admins(request, club_id: UUID):
    club = _get_club(club_id)
    return [ClubAdminOut.of(u, club) for u in club_services.club_admins(club)]


@router.post("/clubs/{club_id}/admins", response={201: ClubAdminOut}, summary="Make someone an administrator of a club")
@idempotent
def add_club_admin(request, club_id: UUID, payload: ClubAdminIn):
    club = _get_club(club_id)
    try:
        user, created = club_services.resolve_account(payload.email, first_name=payload.first_name, last_name=payload.last_name)
    except club_services.UnverifiedEmailError as e:
        raise ApiError(409, "unverified_email", str(e)) from e
    except club_services.AccountError as e:
        raise ApiError(422, "validation_error", str(e), {"first_name": [str(e)]}) from e
    club_services.grant_club_admin(club, user)
    if created:
        club_services.send_password_setup_email(request, user)
    audit.record(request.user, "club.admin_added", target=user, club=club, data={"new_account": created})
    return 201, ClubAdminOut.of(user, club)


@router.delete("/clubs/{club_id}/admins/{user_id}", response={204: None}, summary="Remove someone as club administrator (they stay a member)")
def remove_club_admin(request, club_id: UUID, user_id: UUID):
    club = _get_club(club_id)
    user = get_object_or_404(get_user_model(), pk=user_id)
    if user not in club_services.club_admins(club):
        raise ApiError(404, "not_found", "That user is not an administrator of this club.")
    try:
        club_services.revoke_club_admin(club, user)
    except (club_services.ClubAdminError, ProtectedRoleError) as e:
        raise ApiError(409, "admin_change_refused", str(e)) from e
    audit.record(request.user, "club.admin_removed", target=user, club=club)
    return 204, None
