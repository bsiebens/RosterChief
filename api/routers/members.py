"""Members API (``/api/v1/members``): roster, families, groups and the audit history.

Every visibility decision and every write goes through ``members.services`` (``access`` for who may see whom,
the other modules for writes, which also audit). This module only translates HTTP to service calls. Service
errors are mapped by ``api.errors``: ``NotAllowed`` -> 403, ``Invalid`` -> 422 ``{detail, code, errors}``.

Static paths (``/families``, ``/groups``, ``/history``) are registered before ``/{member_id}`` so they never
clash with it.
"""

import uuid

from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.http import HttpResponse
from ninja import File, Form, Query, Router, UploadedFile
from ninja.errors import HttpError
from ninja.pagination import paginate

from api.schemas_events import MemberAttendanceOut
from api.schemas_import import ImportCommitIn, ImportJobOut, ImportPreviewOut
from api.schemas_members import (
    FamilyIn,
    FamilyMemberIn,
    FamilyOut,
    FamilyPatch,
    FamilyRolePatch,
    GrantLoginIn,
    GrantLoginOut,
    GroupIn,
    GroupMemberPatch,
    GroupOut,
    HistoryEntryOut,
    HistoryFilters,
    MemberCreateIn,
    MemberCreateOut,
    MemberDetail,
    MemberFilters,
    MemberPatch,
    MemberSummary,
)
from api.schemas_officiating import OfficialIn, OfficiatingOptionsOut, OfficiatingOut, ProfileOut, RefereeIn
from club.emails import send_login_granted_email
from events.models import Season
from events.services import attendance_report
from members.models import AuditEntry, ClubMembership, Family, FamilyMembership, Group, ImportJob, Member
from members.services import access, audit, families, groups, importing, officiating, signups
from members.services import members as member_services
from members.services.errors import Invalid
from registration.services import installments
from teams.models import StaffAssignment, TeamMembership

router = Router()

ORDERINGS = {"last_name", "first_name", "date_of_birth", "license_number", "email"}


# -- helpers ---------------------------------------------------------------------------------------------------------


def _is_admin(request) -> bool:
    return access.can_manage_members(request.user)


def _visible_member(request, member_id) -> Member:
    """The member, or 404 when it does not exist *or* the caller may not see it (existence is never leaked)."""
    member = Member.objects.filter(pk=member_id).first()
    if member is None or not access.can_view_member(request.user, member):
        raise HttpError(404, "Not found.")
    return member


def _phone(value) -> str | None:
    return str(value) if value else None


def _phone_display(value) -> str | None:
    """International format with spaces (``+32 470 12 34 56``); falls back to the stored value."""
    if not value:
        return None
    return getattr(value, "as_international", None) or str(value)


def _clean_values(values: dict) -> dict:
    for name in ("phone", "emergency_phone"):
        if name in values and not values[name]:
            values[name] = None
    return values


def _annotate_roles(qs):
    return qs.annotate(
        is_member_flag=Exists(ClubMembership.objects.filter(member=OuterRef("pk"))),
        is_guardian_flag=Exists(Member.objects.guardians().filter(pk=OuterRef("pk"))),
        is_staff_flag=Exists(StaffAssignment.objects.filter(member=OuterRef("pk"))),
    )


def _member_detail(request, member: Member) -> dict:
    admin = _is_admin(request)
    # Everyone who can see a person at all sees the contact details: admins, the person, their guardians and the
    # staff of their team are exactly the set access.members_visible_to() returns, so contact details need no
    # narrower rule here.
    contact = access.can_view_member(request.user, member)
    links = list(member.family_memberships.select_related("family"))
    memberships = member.club_memberships.order_by("-season__start_date")
    if admin:
        memberships = memberships.select_related("registration_details__batch").prefetch_related("payments")
    data = {
        "id": member.pk,
        "first_name": member.first_name,
        "last_name": member.last_name,
        "email": member.email,
        "date_of_birth": member.date_of_birth,
        "language": member.language,
        "license_number": member.license_number,
        "photo_url": member.photo_url,
        "profile_photo_url": member.profile_photo_url,
        "player_photo_url": member.player_photo_url if admin else None,
        "roles": sorted(str(r.value) for r in member.roles),
        "family_ids": sorted({str(link.family_id) for link in links}),
        "has_login": bool(member.user_id),
        "photo_public_consent": member.photo_public_consent,
        "phone": _phone(member.phone) if contact else None,
        "emergency_phone": _phone(member.emergency_phone) if contact else None,
        "phone_display": _phone_display(member.phone) if contact else None,
        "emergency_phone_display": _phone_display(member.emergency_phone) if contact else None,
        "families": [{"id": link.family_id, "name": str(link.family), "role": link.role} for link in links],
        "groups": [{"id": g.group_id, "name": g.group.name} for g in member.group_memberships.select_related("group")] if admin else [],
        "club_memberships": [_club_membership(cm, admin) for cm in memberships],
        "staff_assignments": [
            {"id": sa.pk, "team_id": sa.team_id, "team_name": sa.team.name, "season_id": sa.season_id, "position": sa.position.name} for sa in member.staff_assignments.select_related("team", "position").order_by("-season__start_date")
        ],
        "team_memberships": [
            {"id": tm.pk, "team_id": tm.team_id, "team_name": tm.team.name, "season_id": tm.season_id, "position": tm.position.name if tm.position else None, "jersey_number": tm.jersey_number}
            for tm in member.team_memberships.select_related("team", "position").order_by("-season__start_date")
        ],
    }
    return data


def _club_membership(cm: ClubMembership, admin: bool) -> dict:
    data = {"id": cm.pk, "season_id": cm.season_id, "status": cm.status}
    if admin:
        data.update(
            fee_status=cm.fee_status,
            fee_amount=cm.fee_amount,
            amount_paid=cm.amount_paid,
            early_payment_deadline=cm.early_payment_deadline,
            early_payment_discount=cm.early_payment_discount,
            signed_up_at=cm.signed_up_at,
            activated_at=cm.activated_at,
            next_installment=installments.membership_next(cm),
        )
    return data


def _visible_families(request):
    """Families the caller may read: all for admins, else those with at least one person the caller may see."""
    visible = access.members_visible_to(request.user, include_guardians=True)
    return Family.objects.filter(memberships__member__in=visible).distinct()


def _family_out(request, family: Family) -> dict:
    visible = access.members_visible_to(request.user, include_guardians=True)
    links = family.memberships.filter(member__in=visible).select_related("member")
    return {
        "id": family.pk,
        "name": family.name,
        "display_name": str(family),
        "members": [{"member_id": link.member_id, "first_name": link.member.first_name, "last_name": link.member.last_name, "role": link.role, "email": link.member.email, "has_login": bool(link.member.user_id)} for link in links],
    }


def _get_family(request, family_id, *, write: bool = False) -> Family:
    if write:
        access.require_admin(request.user)
    family = _visible_families(request).filter(pk=family_id).first() if not write else Family.objects.filter(pk=family_id).first()
    if family is None:
        raise HttpError(404, "Not found.")
    return family


def _lookup_member(member_id) -> Member:
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        raise Invalid("Unknown member.", {"member_id": ["Unknown member."]})
    return member


def _group_out(group: Group, *, with_members: bool = True, user=None) -> dict:
    links = group.memberships.select_related("member")
    manager = user is not None and access.can_manage_members(user)
    return {
        "id": group.pk,
        "name": group.name,
        "member_count": links.count(),
        "members": [{"member_id": link.member_id, "first_name": link.member.first_name, "last_name": link.member.last_name, "email": link.member.email, "is_admin": link.is_admin, "must_respond": link.must_respond} for link in links]
        if with_members
        else [],
        "can_manage": manager,
        "can_manage_members": user is not None and access.can_manage_group(user, group),
    }


def _get_group(request, group_id, *, members_only: bool = False) -> Group:
    """403 first; then 404. ``members_only``: a group's own admins pass too (they manage its members)."""
    if members_only:
        if not (access.can_manage_members(request.user) or access.admin_group_ids(request.user)):
            access.require_admin(request.user)
    else:
        access.require_admin(request.user)
    group = Group.objects.filter(pk=group_id).first()
    if group is None:
        raise HttpError(404, "Not found.")
    if members_only:
        access.require_group_manager(request.user, group)
    return group


def _history_out(entry: AuditEntry) -> dict:
    return {
        "id": entry.pk,
        "created": entry.created,
        "actor_id": entry.actor_id,
        "actor_email": entry.actor.email if entry.actor_id else None,
        "member_id": entry.member_id,
        "action": entry.action,
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "target_label": entry.target_label,
        "changes": entry.changes,
    }


class _Mapped:
    """A queryset rendered lazily through ``fn``; supports the count/len/slice that ``paginate`` needs."""

    def __init__(self, qs, fn):
        self.qs, self.fn = qs, fn

    def count(self):
        return self.qs.count()

    __len__ = count

    def __getitem__(self, item):
        if isinstance(item, slice):
            return [self.fn(o) for o in self.qs[item]]
        return self.fn(self.qs[item])


def _history_404_unless_admin(request) -> None:
    if not _is_admin(request):
        raise HttpError(404, "Not found.")


# -- members: list / create ------------------------------------------------------------------------------------------


@router.get("", response=list[MemberSummary], summary="List the members you may see")
@paginate
def list_members(request, filters: Query[MemberFilters]):
    """Paginated roster (``limit``/``offset``; the page is ``{items, count}``).

    Rule: rows come from ``access.members_visible_to``: admins see everyone; others see themselves, their
    children and the roster plus staff of the teams they are staffed on this season. People who are only a
    guardian or only staff are left out unless ``include_guardians=1`` or ``role`` is ``guardian``/``staff``.
    ``status``/``fee_status``/``team`` refer to ``season`` (default: the current season); ``status=open`` means active or pending. Rows carry that season's membership ``status`` (and ``fee_status`` for admins). A person who is both
    member and guardian appears under either ``role`` filter.
    """
    include = filters.include_guardians or filters.role in ("guardian", "staff", "none")
    qs = access.members_visible_to(request.user, include_guardians=include)
    from registration.services.invoicing import awaiting_only_member_ids

    qs = qs.exclude(pk__in=awaiting_only_member_ids())  # people of a registration staff have not confirmed yet are not members yet

    if filters.role == "member":
        qs = qs.filter(pk__in=Member.objects.members(filters.season).values("pk"))
    elif filters.role == "guardian":
        qs = qs.filter(pk__in=Member.objects.guardians().values("pk"))
    elif filters.role == "staff":
        qs = qs.filter(pk__in=Member.objects.staff(filters.season).values("pk"))
    elif filters.role == "none":
        # No role in the season: no membership then, no staff assignment then, and not a guardian.
        role_season = Season.objects.filter(pk=filters.season).first() if filters.season else Season.get_current()
        has_role = Member.objects.guardians().values("pk")
        if role_season is not None:  # without a season nobody has a seasonal role, only the guardian link counts
            has_role = has_role.union(Member.objects.members(role_season).values("pk"), Member.objects.staff(role_season).values("pk"))
        qs = qs.exclude(pk__in=has_role)
    elif filters.season:
        qs = qs.filter(pk__in=Member.objects.members(filters.season).values("pk"))

    if filters.q:
        for term in filters.q.split():
            qs = qs.filter(Q(first_name__icontains=term) | Q(last_name__icontains=term) | Q(email__icontains=term) | Q(license_number__icontains=term))

    season = Season.objects.filter(pk=filters.season).first() if filters.season else Season.get_current()
    if filters.documents == "pending":
        from members.models import MemberRequirementStatus

        qs = qs.filter(pk__in=MemberRequirementStatus.objects.filter(is_pending_review=True, membership__season=season).values("membership__member_id")) if season else qs.none()
    if filters.status or filters.fee_status or filters.team:
        if season is None:
            return Member.objects.none()
        if filters.status or filters.fee_status:
            cms = ClubMembership.objects.filter(member=OuterRef("pk"), season=season)
            if filters.status == "open":  # the old panel's default: active and pending, hiding lapsed/cancelled
                cms = cms.filter(status__in=[ClubMembership.StatusChoices.ACTIVE, ClubMembership.StatusChoices.PENDING])
            elif filters.status:
                cms = cms.filter(status=filters.status)
            if filters.fee_status:
                cms = cms.filter(fee_status=filters.fee_status)
            qs = qs.filter(Exists(cms))
        if filters.team:
            on_roster = TeamMembership.objects.filter(member=OuterRef("pk"), season=season)
            on_staff = StaffAssignment.objects.filter(member=OuterRef("pk"), season=season)
            if filters.team == "none":  # on no team's roster or staff this season
                qs = qs.exclude(Exists(on_roster) | Exists(on_staff))
            else:
                try:
                    team_id = uuid.UUID(filters.team)
                except ValueError:
                    raise Invalid("Unknown team.", {"team": ["Unknown team."]}) from None
                qs = qs.filter(Exists(on_roster.filter(team_id=team_id)) | Exists(on_staff.filter(team_id=team_id)))

    # The season's membership status on every row (one subquery, no N+1); fee status only for admins.
    if season is not None:
        current = ClubMembership.objects.filter(member=OuterRef("pk"), season=season)
        qs = qs.annotate(status=Subquery(current.values("status")[:1]))
        if access.can_manage_members(request.user):
            qs = qs.annotate(fee_status=Subquery(current.values("fee_status")[:1]))

    field = filters.ordering.lstrip("-")
    if field not in ORDERINGS:
        raise Invalid("Unknown ordering.", {"ordering": [f"Choose one of {sorted(ORDERINGS)} (optionally prefixed with -)."]})
    order = [filters.ordering]
    if field != "last_name":
        order.append("last_name")
    order += ["first_name", "pk"]
    return _annotate_roles(qs).prefetch_related("family_memberships__family__memberships__member").order_by(*order)


def _invite(request, member, role_slug: str) -> tuple[dict, tuple]:
    """Grant the login now (inside the creating transaction, so a missing email undoes the whole add); the email goes out after."""
    user, created = member_services.grant_login(request.user, request.tenant, member, role_slug=role_slug)
    return {"user_created": created, "email": user.email}, (member, user, created)


@router.post("", response={201: MemberCreateOut}, summary="Add a member (admin)")
def create_member(request, payload: MemberCreateIn):
    """Create a person on the roster. Rule: admin only (403 otherwise). Respects the club's member limit (403 ``limit_exceeded``).
    Optionally in the same step (all or nothing, 422 undoes everything): a season ``membership`` (``active`` skips the sign-up queue,
    ``pending`` joins it), a ``family`` to join or ``guardians`` (existing or new people) who become the parents of a new family,
    and an ``invite`` (a login plus the set-password email, which needs an email address). Writes audit entries."""
    access.require_admin(request.user)
    data = payload.dict()
    membership_in, family_in, guardians_in, invite_in = (data.pop(k) for k in ("membership", "family", "guardians", "invite"))
    values = _clean_values(data)
    first, last, email = values.pop("first_name"), values.pop("last_name"), values.pop("email")
    features = getattr(request, "features", None)
    emails: list[tuple] = []
    with transaction.atomic():
        member = member_services.create_member(request.user, request.tenant, first_name=first, last_name=last, email=email, features=features, **values)
        membership = None
        if membership_in is not None:
            season = Season.objects.filter(pk=membership_in["season_id"]).first() if membership_in["season_id"] else Season.get_current()
            if season is None:
                raise Invalid("There is no current season to sign the person up for.", {"membership.season_id": ["Choose a season."]})
            membership = signups.enrol(request.user, member, season, status=membership_in["status"], fee_amount=membership_in["fee_amount"], waived=membership_in["waived"])
        guardians = []
        family = None
        if family_in is not None:
            family = Family.objects.filter(pk=family_in["family_id"]).first()
            if family is None:
                raise Invalid("Unknown family.", {"family.family_id": ["Unknown family."]})
            families.add_member(request.user, family, member, family_in["role"])
        elif guardians_in:
            family = families.create_family(request.user)
            families.add_member(request.user, family, member, FamilyMembership.FamilyRole.CHILD)
        guardian_results = []
        for g in guardians_in:
            if g["member_id"]:
                guardian = Member.objects.filter(pk=g["member_id"]).first()
                if guardian is None:
                    raise Invalid("Unknown guardian.", {"guardians": ["Unknown member."]})
            else:
                guardian = member_services.create_member(request.user, request.tenant, first_name=g["first_name"], last_name=g["last_name"], email=g["email"], features=features)
            families.add_member(request.user, family, guardian, FamilyMembership.FamilyRole.PARENT)
            guardians.append(guardian)
            if g["invite"]:
                info, mail = _invite(request, guardian, "member")
                guardian_results.append(info)
                emails.append(mail)
        invite_result = None
        if invite_in is not None and invite_in["send"]:
            invite_result, mail = _invite(request, member, invite_in["role_slug"])
            emails.append(mail)
    sent = {m.pk: send_login_granted_email(request, request.tenant, m, user, user_created=created) for m, user, created in emails}
    detail = _member_detail(request, member)
    detail.update(
        membership_id=membership.pk if membership else None,
        guardian_ids=[g.pk for g in guardians],
        invite={**invite_result, "sent": sent[member.pk]} if invite_result else None,
        guardian_invites=[{**info, "sent": sent[g.pk]} for g, info in zip([g for g, gi in zip(guardians, guardians_in, strict=True) if gi["invite"]], guardian_results, strict=True)],
    )
    return 201, detail


# -- families (registered before /{member_id}) ----------------------------------------------------------------------


# -- mass import (registered before /{member_id}) -------------------------------------------------------------------


@router.get("/import/template", summary="A CSV template for the member import (admin)", operation_id="member_import_template")
def import_template(request):
    access.require_admin(request.user)
    response = HttpResponse(importing.template_csv(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="members-template.csv"'
    return response


@router.post("/import/preview", response=ImportPreviewOut, summary="Check an uploaded .csv / .xlsx file without saving anything (admin)", operation_id="member_import_preview")
def import_preview(request, file: File[UploadedFile], season_id: uuid.UUID | None = Form(None)):
    """Multipart: ``file`` (.csv or .xlsx, up to 5 MB and 2000 rows) and an optional ``season_id``. Returns every row
    with its status (new, duplicate, error) and the messages per column. Nothing is created."""
    access.require_admin(request.user)
    return importing.preview(request.user, file, season_id)


@router.post("/import/commit", response={202: ImportJobOut}, summary="Start creating the members of a previewed import in the background (admin)", operation_id="member_import_commit")
def import_commit(request, payload: ImportCommitIn):
    """Send the rows of the preview back (only the ones to create are needed). A background job validates them again,
    skips duplicates, reports rows with errors and creates the rest (member, membership for the season, roster spot,
    family link). Poll ``/import/jobs/{id}`` for progress and the result; the starter also gets an inbox note."""
    access.require_admin(request.user)
    job = importing.start_import(request.user, [r.dict() for r in payload.rows], payload.season_id)
    return 202, _job_out(job)


@router.get("/import/jobs/{job_id}", response=ImportJobOut, summary="Progress and result of an import job (admin)", operation_id="member_import_job")
def import_job(request, job_id: uuid.UUID):
    access.require_admin(request.user)
    job = ImportJob.objects.filter(pk=job_id).first()
    if job is None:
        raise HttpError(404, "Not found.")
    return _job_out(job)


def _job_out(job) -> dict:
    return {"id": job.pk, "status": job.status, "total": job.total, "processed": job.processed, "error": job.error, "result": job.result}


@router.get("/teams", summary="Teams, for the member list's team filter")
def team_options(request):
    """``[{id, name}]`` of every team, alphabetical. Names are not sensitive within a club, so any signed-in club user
    may read them; who is *on* a team stays governed by the member visibility rules."""
    from teams.models import Team

    return [{"id": str(t.pk), "name": t.name} for t in Team.objects.order_by("name")]


def _officiating_member(request, member_id=None, *, write=False):
    """Only for clubs with the ``referee_officials`` feature (404 otherwise, as if it did not exist). Everyone reads;
    writing needs ``members.manage_members``."""
    if write:
        access.require_admin(request.user)
    if not request.features.has("referee_officials"):
        raise HttpError(404, "Not found.")
    return _visible_member(request, member_id) if member_id is not None else None


@router.get("/officiating/options", response=OfficiatingOptionsOut, summary="Referee levels, official levels and teams (referee_officials feature)")
def officiating_options(request):
    """Choices for the referee / official forms."""
    _officiating_member(request)
    return officiating.options()


@router.get("/families", response=list[FamilyOut], summary="List families")
@paginate
def list_families(request, q: str | None = None, role: str | None = None):
    """Rule: admins see all families; others see families that contain someone they may see, and only those people.
    Filters: ``q`` (every word matches the family name or the name of one of its people), ``role`` (the family has
    someone with that family role: parent, child, guardian or other)."""
    qs = _visible_families(request)
    for word in (q or "").split():
        qs = qs.filter(Q(name__icontains=word) | Exists(FamilyMembership.objects.filter(family=OuterRef("pk")).filter(Q(member__first_name__icontains=word) | Q(member__last_name__icontains=word))))
    if role:
        qs = qs.filter(Exists(FamilyMembership.objects.filter(family=OuterRef("pk"), role=role)))
    # Alphabetical by what the list shows: the family name, else its first surname (a nameless family would sort first otherwise).
    first_surname = FamilyMembership.objects.filter(family=OuterRef("pk")).exclude(member__last_name="").order_by("member__last_name").values("member__last_name")[:1]
    qs = qs.annotate(sort_name=Lower(Coalesce(NullIf("name", Value("")), Subquery(first_surname), Value("~"))))
    return _Mapped(qs.order_by("sort_name", "pk"), lambda f: _family_out(request, f))


@router.post("/families", response={201: FamilyOut}, summary="Create a family (admin)")
def create_family(request, payload: FamilyIn):
    """Rule: admin only. Optionally with initial ``members`` ({member_id, role}). Audited."""
    access.require_admin(request.user)
    pairs = [(_lookup_member(m.member_id), m.role) for m in payload.members]
    family = families.create_family(request.user, name=payload.name, members=pairs)
    return 201, _family_out(request, family)


@router.get("/families/{family_id}", response=FamilyOut, summary="One family")
def get_family(request, family_id: uuid.UUID):
    """Rule: 404 unless the caller may see at least one person in the family; only visible people are listed."""
    return _family_out(request, _get_family(request, family_id))


@router.patch("/families/{family_id}", response=FamilyOut, summary="Rename a family (admin)")
def patch_family(request, family_id: uuid.UUID, payload: FamilyPatch):
    """Rule: admin only (403 otherwise)."""
    family = _get_family(request, family_id, write=True)
    return _family_out(request, families.rename_family(request.user, family, payload.name))


@router.delete("/families/{family_id}", response={204: None}, summary="Delete a family (admin)")
def delete_family(request, family_id: uuid.UUID):
    """Rule: admin only. Removes the family and its links; the people stay."""
    families.delete_family(request.user, _get_family(request, family_id, write=True))
    return 204, None


@router.post("/families/{family_id}/members", response={201: FamilyOut}, summary="Add a person to a family (admin)")
def add_family_member(request, family_id: uuid.UUID, payload: FamilyMemberIn):
    """Rule: admin only. 422 when the person is already in the family or the role is unknown."""
    family = _get_family(request, family_id, write=True)
    families.add_member(request.user, family, _lookup_member(payload.member_id), payload.role)
    return 201, _family_out(request, family)


@router.patch("/families/{family_id}/members/{member_id}", response=FamilyOut, summary="Change a family role (admin)")
def patch_family_member(request, family_id: uuid.UUID, member_id: uuid.UUID, payload: FamilyRolePatch):
    """Rule: admin only. 422 when the person is not in the family or the role is unknown."""
    family = _get_family(request, family_id, write=True)
    families.change_role(request.user, family, _lookup_member(member_id), payload.role)
    return _family_out(request, family)


@router.delete("/families/{family_id}/members/{member_id}", response={204: None}, summary="Remove a person from a family (admin)")
def delete_family_member(request, family_id: uuid.UUID, member_id: uuid.UUID):
    """Rule: admin only. 422 when the person is not in the family."""
    family = _get_family(request, family_id, write=True)
    families.remove_member(request.user, family, _lookup_member(member_id))
    return 204, None


# -- groups ----------------------------------------------------------------------------------------------------------


@router.get("/groups", response=list[GroupOut], summary="List groups (admin; a group admin sees their own groups)")
@paginate
def list_groups(request, q: str | None = None):
    """Rule: members managers see every group, a group's admins their own (403 for anyone else). ``q``: every word matches the group name or the name of one of its people."""
    user = request.user
    qs = Group.objects.order_by("name")
    if not access.can_manage_members(user):
        ids = access.admin_group_ids(user)
        if not ids:
            access.require_admin(user)
        qs = qs.filter(pk__in=ids)
    for word in (q or "").split():
        qs = qs.filter(Q(name__icontains=word) | Q(memberships__member__first_name__icontains=word) | Q(memberships__member__last_name__icontains=word)).distinct()
    return _Mapped(qs, lambda g: _group_out(g, with_members=False, user=user))


@router.post("/groups", response={201: GroupOut}, summary="Create a group (admin)")
def create_group(request, payload: GroupIn):
    """Rule: admin only. 422 for an empty or duplicate name."""
    access.require_admin(request.user)
    return 201, _group_out(groups.create_group(request.user, payload.name), user=request.user)


@router.get("/groups/{group_id}", response=GroupOut, summary="One group with its members (admin or group admin)")
def get_group(request, group_id: uuid.UUID):
    """Rule: members managers, or an admin of this group. ``is_admin`` / ``must_respond`` per person."""
    return _group_out(_get_group(request, group_id, members_only=True), user=request.user)


@router.patch("/groups/{group_id}", response=GroupOut, summary="Rename a group (admin)")
def patch_group(request, group_id: uuid.UUID, payload: GroupIn):
    """Rule: admin only. 422 for an empty or duplicate name."""
    group = _get_group(request, group_id)
    return _group_out(groups.rename_group(request.user, group, payload.name), user=request.user)


@router.delete("/groups/{group_id}", response={204: None}, summary="Delete a group (admin)")
def delete_group(request, group_id: uuid.UUID):
    """Rule: admin only. The people stay."""
    groups.delete_group(request.user, _get_group(request, group_id))
    return 204, None


@router.post("/groups/{group_id}/members/{member_id}", response={201: GroupOut}, summary="Add a person to a group (admin or group admin)")
def add_group_member(request, group_id: uuid.UUID, member_id: uuid.UUID, is_admin: bool = False, must_respond: bool = True):
    """Rule: members managers, or an admin of this group (only members managers may add an admin). 422 when already in the group.
    Optional query parameters ``is_admin`` (default false) and ``must_respond`` (default true)."""
    group = _get_group(request, group_id, members_only=True)
    groups.add_member(request.user, group, _lookup_member(member_id), is_admin=is_admin, must_respond=must_respond)
    return 201, _group_out(group, user=request.user)


@router.patch("/groups/{group_id}/members/{member_id}", response=GroupOut, summary="Set whether a person administers the group / must respond to its invites")
def update_group_member(request, group_id: uuid.UUID, member_id: uuid.UUID, payload: GroupMemberPatch):
    """``is_admin``: members managers only. ``must_respond``: members managers and the group's admins. 422 when not in the group."""
    group = _get_group(request, group_id, members_only=True)
    groups.update_member(request.user, group, _lookup_member(member_id), **payload.dict(exclude_unset=True))
    return _group_out(group, user=request.user)


@router.delete("/groups/{group_id}/members/{member_id}", response={204: None}, summary="Remove a person from a group (admin or group admin)")
def remove_group_member(request, group_id: uuid.UUID, member_id: uuid.UUID):
    """Rule: members managers, or an admin of this group. 422 when not in the group."""
    group = _get_group(request, group_id, members_only=True)
    groups.remove_member(request.user, group, _lookup_member(member_id))
    return 204, None


# -- audit history (registered before /{member_id}) ------------------------------------------------------------------


@router.get("/history", response=list[HistoryEntryOut], summary="Audit history of the whole club (admin)")
@paginate
def club_history(request, filters: Query[HistoryFilters]):
    """Rule: admin only; 404 for everyone else. Newest first. Filters: ``actor`` (user id), ``action`` (exact name or
    prefix such as ``family.``), ``target_type``, ``since`` (datetime)."""
    _history_404_unless_admin(request)
    qs = AuditEntry.objects.select_related("actor").order_by("-created", "-pk")
    if filters.actor:
        try:
            qs = qs.filter(actor__pk=filters.actor)
        except ValueError, TypeError:
            raise Invalid("Unknown actor.", {"actor": ["Not a valid user id."]}) from None
    if filters.action:
        qs = qs.filter(action__startswith=filters.action)
    if filters.target_type:
        qs = qs.filter(target_type=filters.target_type)
    if filters.since:
        qs = qs.filter(created__gte=filters.since)
    return _Mapped(qs, _history_out)


# -- attendance statistics (registered before /{member_id}) ---------------------------------------------------------


@router.get("/{member_id}/attendance", response=MemberAttendanceOut, summary="A member's attendance for a season", operation_id="member_attendance")
def member_attendance(request, member_id: uuid.UUID, season: uuid.UUID | None = None):
    """Rule: 404 unless the caller can see the member. Season totals over past events, the turnout, the last 12 events
    (sparkline) and the 10 most recent past events with the answer. Coach notes are never part of it."""
    member = _visible_member(request, member_id)
    chosen = None
    if season is not None:
        chosen = Season.objects.filter(pk=season).first()
        if chosen is None:
            raise Invalid("Unknown season.", {"season": ["Unknown season."]})
    return attendance_report.member_report(member, chosen)


# -- one member ------------------------------------------------------------------------------------------------------


@router.get("/{member_id}", response=MemberDetail, summary="One member")
def get_member(request, member_id: uuid.UUID):
    """Rule: 404 (never 403) unless ``access.can_view_member``: admins see everyone; others themselves, their
    children and the roster/staff of the teams they are staffed on.

    Contact details (phone, emergency phone) are shown to every viewer who can see the person: that set is
    exactly admin, the person themself, their guardian, or staff of their team. Club membership fee fields
    (fee status, amounts, deadlines) are admin only; others get ``status`` only. ``groups`` is admin only.
    """
    return _member_detail(request, _visible_member(request, member_id))


@router.patch("/{member_id}", response=MemberDetail, summary="Edit a member (admin)")
def patch_member(request, member_id: uuid.UUID, payload: MemberPatch):
    """Rule: 404 if the caller cannot see the member, 403 if they can but are not an admin. Only sent fields change;
    422 for invalid values. Audited with the field diff."""
    member = _visible_member(request, member_id)
    values = _clean_values(payload.dict(exclude_unset=True))
    member_services.update_member(request.user, member, **values)
    return _member_detail(request, member)


@router.delete("/{member_id}", response={204: None}, summary="Delete a member (admin)")
def delete_member(request, member_id: uuid.UUID, detach_login: bool = False):
    """Rule: admin only (404 if not visible). 422 when it is your own record, the person has a login and
    ``detach_login`` is false, or they have recorded fee payments. The user account is kept either way."""
    member = _visible_member(request, member_id)
    member_services.delete_member(request.user, member, detach_login=detach_login)
    return 204, None


@router.post("/{member_id}/photo", response=MemberDetail, summary="Upload the profile picture (admin)")
def upload_photo(request, member_id: uuid.UUID, photo: File[UploadedFile]):
    """Multipart, field ``photo``: JPEG, PNG or WebP up to 5 MB (422 otherwise). Replaces the current profile picture."""
    member = _visible_member(request, member_id)
    member_services.set_profile_photo(request.user, member, photo)
    return _member_detail(request, member)


@router.delete("/{member_id}/photo", response=MemberDetail, summary="Remove the profile picture (admin)")
def delete_photo(request, member_id: uuid.UUID):
    member = _visible_member(request, member_id)
    member_services.remove_profile_photo(request.user, member)
    return _member_detail(request, member)


@router.post("/{member_id}/player-photo", response=MemberDetail, summary="Upload the official player picture (admin)")
def upload_player_photo(request, member_id: uuid.UUID, photo: File[UploadedFile]):
    """Multipart, field ``photo``: JPEG, PNG or WebP up to 5 MB (422 otherwise). The player picture is public only
    once ``photo_public_consent`` is set."""
    member = _visible_member(request, member_id)
    member_services.set_photo(request.user, member, photo, "player_photo")
    return _member_detail(request, member)


@router.delete("/{member_id}/player-photo", response=MemberDetail, summary="Remove the official player picture (admin)")
def delete_player_photo(request, member_id: uuid.UUID):
    member = _visible_member(request, member_id)
    member_services.remove_photo(request.user, member, "player_photo")
    return _member_detail(request, member)


@router.get("/{member_id}/officiating", response=OfficiatingOut, summary="Referee and official eligibility (referee_officials feature)")
def get_officiating(request, member_id: uuid.UUID):
    """Each of ``referee`` / ``official`` is null when the member has no such profile. ``eligible_teams`` is empty unless
    the profile has a level and its validity date has not passed."""
    return officiating.officiating(_officiating_member(request, member_id))


@router.put("/{member_id}/referee", response=ProfileOut, summary="Set the referee profile (admin; referee_officials feature)")
def put_referee(request, member_id: uuid.UUID, payload: RefereeIn):
    """Creates or updates the single referee profile. 422 for an unknown level or team."""
    member = _officiating_member(request, member_id, write=True)
    profile = officiating.save_profile(request.user, member, officiating.REFEREE, level_id=payload.level_id, valid_until=payload.valid_until, excluded_team_ids=payload.excluded_team_ids)
    return officiating.profile_out(profile)


@router.delete("/{member_id}/referee", response={204: None}, summary="Remove the referee profile (admin; referee_officials feature)")
def delete_referee(request, member_id: uuid.UUID):
    officiating.remove_profile(request.user, _officiating_member(request, member_id, write=True), officiating.REFEREE)
    return 204, None


@router.put("/{member_id}/official", response=ProfileOut, summary="Set the official profile (admin; referee_officials feature)")
def put_official(request, member_id: uuid.UUID, payload: OfficialIn):
    """Creates or updates the single official profile. 422 for an unknown level."""
    member = _officiating_member(request, member_id, write=True)
    profile = officiating.save_profile(request.user, member, officiating.OFFICIAL, level_id=payload.level_id, valid_until=payload.valid_until)
    return officiating.profile_out(profile)


@router.delete("/{member_id}/official", response={204: None}, summary="Remove the official profile (admin; referee_officials feature)")
def delete_official(request, member_id: uuid.UUID):
    officiating.remove_profile(request.user, _officiating_member(request, member_id, write=True), officiating.OFFICIAL)
    return 204, None


@router.post("/{member_id}/grant-login", response=GrantLoginOut, summary="Give a member a login (admin)")
def grant_login(request, member_id: uuid.UUID, payload: GrantLoginIn | None = None):
    """Rule: admin only. Uses the member's email as the login. Returns ``user_created``; when true the person has no
    password yet. The person is emailed (club logo and colours, a link to set a password straight away for a new
    account, the sign-in URL); ``email_sent`` is false when the platform's automated-email pause is on. 422 when the member has no email or already has a login."""
    member = _visible_member(request, member_id)
    user, created = member_services.grant_login(request.user, request.tenant, member, role_slug=(payload.role_slug if payload else "member"))
    sent = send_login_granted_email(request, request.tenant, member, user, user_created=created)
    return {"member_id": member.pk, "user_id": user.pk, "email": user.email, "user_created": created, "email_sent": sent}


@router.get("/{member_id}/history", response=list[HistoryEntryOut], summary="Audit history of one member (admin)")
@paginate
def member_history(request, member_id: uuid.UUID):
    """Rule: admin only; 404 for everyone else (and for an unknown member). Newest first."""
    _history_404_unless_admin(request)
    member = Member.objects.filter(pk=member_id).first()
    if member is None:
        raise HttpError(404, "Not found.")
    return _Mapped(audit.history(member), _history_out)
