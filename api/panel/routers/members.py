"""Management API: members, their current-season membership, and families (API.md §17).

The JSON counterpart of the ``/manage`` members, memberships and families screens
(management/views.py). Who may do what is the web's rule, decided by club.services.access:

* reading people and families: any management staff (``panel_club``), narrowed to
  ``members_visible_to`` -- an admin sees everyone, a coach only their own teams' people;
* changing them: ``can_manage_members`` (club admin or member admin);
* a membership's status and fee fields, and the season-wide membership list: club admins only.

Validation is the web forms' (management.forms), side effects are the existing services
(members.services.*); nothing is re-implemented here: the list filters are members.services.listing / club.services.fees.

Not covered (web-only for now): member import (file upload), the member's photo upload, referee /
official eligibility, onboarding-requirement actions, dues invoices and payment recording.
"""

import uuid
from typing import Annotated, Literal

from django.db import transaction
from django.db.models import F, ProtectedError, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from api.core.errors import ApiError
from api.core.forms import form_error
from api.core.idempotency import idempotent
from api.core.pagination import paginate_keyset
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas import members as s
from club.models import ClubMembership, Season
from club.services.access import can_manage_members, current_season, is_club_admin, members_visible_to
from club.services.fees import season_memberships
from management.forms import AddChildForm, AddParentForm, AttachToFamilyForm, ClubMembershipForm, FamilyCreateForm, GrantLoginForm, MemberForm
from members.models import Family, FamilyMembership, Member
from members.services.deletion import member_has_ties_outside_club, remove_member_from_club
from members.services.family import add_child_to_family, attach_to_family, detach_from_family, families_of_club, grant_login
from members.services.invites import add_or_invite_parent, pending_invites, register_family_or_invite
from members.services.listing import filter_members

router = Router(tags=["panel: members"])

PREFIX = "/manage/members"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}

PERSON_FIELDS = ("first_name", "last_name", "date_of_birth", "email", "phone", "emergency_phone", "photo_public_consent")
ADMIN_ONLY_MEMBERSHIP_FIELDS = ("status", "fee_status", "fee_amount")
FIELD_ALIASES = {"family": "family_id"}


# --- helpers -----------------------------------------------------------------------------------


def _visible(request, club):
    return members_visible_to(request.user, club, include_guardians=True)


def _member(request, club, member_id) -> Member:
    return get_object_or_404(_visible(request, club), pk=member_id)


def _family(club, family_id) -> Family:
    return get_object_or_404(families_of_club(club), pk=family_id)


def _membership_out(membership: ClubMembership) -> dict:
    return {
        "id": membership.pk,
        "season_id": membership.season_id,
        "season": str(membership.season),
        "kind": membership.kind,
        "license": membership.license,
        "status": membership.status,
        "fee_status": membership.fee_status,
        "fee_amount": membership.fee_amount,
        "amount_paid": membership.amount_paid,
        "signed_up_at": membership.signed_up_at,
    }


def _current_membership(club, member):
    season = current_season(club)
    if season is None:
        return None
    return ClubMembership.objects.filter(club=club, member=member, season=season).select_related("season").first()


def _person_out(family_membership: FamilyMembership) -> dict:
    member = family_membership.member
    return {"member_id": member.pk, "first_name": member.first_name, "last_name": member.last_name, "role": family_membership.role, "has_login": member.user_id is not None}


GUARDIAN_ROLES = (FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN)


def _split(rows) -> tuple[list, list, list]:
    guardians, children, others = [], [], []
    for row in rows:
        bucket = guardians if row.role in GUARDIAN_ROLES else children if row.role == FamilyMembership.FamilyRole.CHILD else others
        bucket.append(_person_out(row))
    return guardians, children, others


def _family_out(request, club, family: Family) -> dict:
    """Everyone of the family the caller may see (a coach doesn't see family-mates outside their teams)."""
    rows = FamilyMembership.objects.filter(family=family, member__in=_visible(request, club)).select_related("member").distinct()
    guardians, children, others = _split(rows)
    invites = [{"email": i.email, "first_name": i.first_name, "last_name": i.last_name, "expires_at": i.expires_at} for i in pending_invites(family)]
    return {"id": family.pk, "name": str(family), "guardians": guardians, "children": children, "others": others, "pending_invites": invites}


def _guardians_of(club, member) -> list:
    rows = FamilyMembership.objects.filter(family__club=club, role__in=GUARDIAN_ROLES, family__memberships__member=member).exclude(member=member).select_related("member").distinct()
    return [_person_out(row) for row in rows]


def member_detail(request, club, member: Member) -> dict:
    family_ids = FamilyMembership.objects.filter(member=member, family__club=club).values_list("family_id", flat=True)
    families = [_family_out(request, club, family) for family in families_of_club(club).filter(pk__in=family_ids)]
    membership = _current_membership(club, member)
    is_child = FamilyMembership.objects.filter(member=member, family__club=club, role=FamilyMembership.FamilyRole.CHILD).exists()
    return {
        "id": member.pk,
        "first_name": member.first_name,
        "last_name": member.last_name,
        "date_of_birth": member.date_of_birth,
        "email": member.email,
        "phone": str(member.phone) if member.phone else None,
        "emergency_phone": str(member.emergency_phone) if member.emergency_phone else None,
        "photo_public_consent": member.photo_public_consent,
        "player_photo_url": member.player_photo_url,
        "has_login": member.user_id is not None,
        "person_fields_locked": member_has_ties_outside_club(member, club),
        "membership": _membership_out(membership) if membership else None,
        "families": families,
        "guardians": _guardians_of(club, member) if is_child else [],
    }


def _person_data(member: Member) -> dict:
    return {
        "first_name": member.first_name,
        "last_name": member.last_name,
        "date_of_birth": member.date_of_birth.isoformat() if member.date_of_birth else "",
        "email": member.email,
        "phone": str(member.phone) if member.phone else "",
        "emergency_phone": str(member.emergency_phone) if member.emergency_phone else "",
        "photo_public_consent": member.photo_public_consent,
    }


def _blank_none(values: dict) -> dict:
    return {key: ("" if value is None else value) for key, value in values.items()}


# --- members -----------------------------------------------------------------------------------


@router.get(PREFIX, response={200: s.MemberListOut, **ERRORS}, summary="List members")
def list_members(
    request,
    q: Annotated[str | None, Query(description="Matches first name, last name or email.")] = None,
    kind: Annotated[Literal["member", "guardian", "staff", "both"], Query(description="`member` (default) hides guardians and staff; `both` shows everyone.")] = "member",
    status: Annotated[str | None, Query(description="A membership status of the current season, or `all`. Default: lapsed and cancelled people are hidden.")] = None,
    fee_status: Annotated[str | None, Query(description="Current-season fee status.")] = None,
    team_id: uuid.UUID | None = None,
    unrostered: Annotated[bool, Query(description="Only members without a team this season.")] = False,
    open_documents: Annotated[bool, Query(description="Only members with open onboarding requirements.")] = False,
    cursor: str | None = None,
    limit: int | None = None,
):
    """Everyone the caller may see, by last name. Management staff; a coach sees their teams' people."""
    club = panel_club(request)
    season = current_season(club)
    members = filter_members(request.user, club, season, kind=kind, status=status or "", fee_status=fee_status or "", team_id=team_id or "", unrostered=unrostered, open_documents=open_documents, q=q or "")

    page = paginate_keyset(members, fields=["last_name", "first_name", "pk"], cursor=cursor, limit=limit)
    ids = [member.pk for member in page.items]
    memberships = {}
    if season is not None:
        memberships = {m.member_id: m for m in ClubMembership.objects.filter(club=club, season=season, member_id__in=ids).select_related("season")}
    families: dict = {}
    for row in FamilyMembership.objects.filter(member_id__in=ids, family__club=club).select_related("family"):
        families.setdefault(row.member_id, []).append({"family_id": row.family_id, "name": str(row.family), "role": row.role})
    items = [
        {
            "id": member.pk,
            "first_name": member.first_name,
            "last_name": member.last_name,
            "email": member.email,
            "has_login": member.user_id is not None,
            "membership": _membership_out(memberships[member.pk]) if member.pk in memberships else None,
            "families": families.get(member.pk, []),
        }
        for member in page.items
    ]
    return {"items": items, "next_cursor": page.next_cursor}


@router.post(PREFIX, response={201: s.MemberDetailOut, **ERRORS, **INVALID}, summary="Add a member")
def create_member(request, payload: s.MemberCreateIn):
    """Creates the person and signs them up (active) for the current season, like the web form.
    Without a current season the person is created unrostered (`membership: null`). Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)

    def work():
        form = MemberForm(_blank_none(payload.model_dump()))
        if not form.is_valid():
            raise form_error(form, FIELD_ALIASES)
        with transaction.atomic():
            member = form.save()
            season = current_season(club)
            if season is not None:
                ClubMembership.objects.create(club=club, member=member, season=season, status=ClubMembership.StatusChoices.ACTIVE, signed_up_at=timezone.localdate())
        return 201, member_detail(request, club, member)

    return idempotent(request, work)


@router.get(PREFIX + "/memberships", response={200: s.MembershipListOut, **ERRORS}, summary="Memberships of a season (fees)")
def list_memberships(
    request,
    season_id: uuid.UUID | None = None,
    fee_status: Annotated[str | None, Query(description="Default `not_paid` (unpaid or partially paid); `all` or a single fee status.")] = "not_paid",
    status: Annotated[str | None, Query(description="A membership status, or `all` (default).")] = "all",
    team_id: uuid.UUID | None = None,
    q: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
):
    """Who has paid for a season (default: the current one); member-kind memberships only. Club admins
    only, as the web screen. Search also matches family names and the registration's invoice or contact, as the web screen."""
    club = panel_club(request)
    if not is_club_admin(request.user, club):
        raise ApiError(403, "forbidden", _("You don't have permission to do this."))
    season = get_object_or_404(Season, pk=season_id, club=club) if season_id else current_season(club)
    if season is None:
        return {"season_id": None, "items": [], "next_cursor": None}

    memberships = season_memberships(club, season, fee_status=fee_status or "all", status=status or "all", team_id=team_id or "", q=q or "")

    page = paginate_keyset(memberships.annotate(sort_last=F("member__last_name"), sort_first=F("member__first_name")), fields=["sort_last", "sort_first", "pk"], cursor=cursor, limit=limit)
    items = [{**_membership_out(m), "member_id": m.member_id, "first_name": m.member.first_name, "last_name": m.member.last_name} for m in page.items]
    return {"season_id": season.pk, "items": items, "next_cursor": page.next_cursor}


@router.get(PREFIX + "/families", response={200: s.FamilyListOut, **ERRORS}, summary="List families")
def list_families(request, q: Annotated[str | None, Query(description="Matches the first or last name of anyone in the family.")] = None, cursor: str | None = None, limit: int | None = None):
    """Families with at least one person the caller may see, with the people they may see."""
    club = panel_club(request)
    visible = _visible(request, club)
    if q and q.strip():
        term = q.strip()
        visible = visible.filter(Q(first_name__icontains=term) | Q(last_name__icontains=term))
    families = families_of_club(club).filter(pk__in=FamilyMembership.objects.filter(member__in=visible).values("family_id"))
    page = paginate_keyset(families, fields=["name", "pk"], cursor=cursor, limit=limit)

    visible_all = _visible(request, club)
    rows = FamilyMembership.objects.filter(family__in=page.items, member__in=visible_all).select_related("member").distinct()
    by_family: dict = {}
    for row in rows:
        by_family.setdefault(row.family_id, []).append(row)
    items = []
    for family in page.items:
        guardians, children, _others = _split(by_family.get(family.pk, []))
        items.append({"id": family.pk, "name": str(family), "guardians": guardians, "children": children})
    return {"items": items, "next_cursor": page.next_cursor}


@router.post(PREFIX + "/families", response={201: s.FamilyOut, **ERRORS, **INVALID}, summary="Register a new family")
def create_family(request, payload: s.FamilyCreateIn):
    """A parent (gets a login, or is emailed an invite if the address already has an account) and a
    child, signed up for the current season. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)

    def work():
        form = FamilyCreateForm(_blank_none(payload.model_dump()))
        if not form.is_valid():
            raise form_error(form, FIELD_ALIASES)
        family = register_family_or_invite(club, current_season(club), invited_by=request.user, request=request, **form.cleaned_data)
        return 201, _family_out(request, club, family)

    return idempotent(request, work)


@router.get(PREFIX + "/families/{uuid:family_id}", response={200: s.FamilyOut, **ERRORS}, summary="One family")
def get_family(request, family_id: uuid.UUID):
    club = panel_club(request)
    return _family_out(request, club, _family(club, family_id))


@router.post(PREFIX + "/families/{uuid:family_id}/children", response={201: s.FamilyOut, **ERRORS, **INVALID}, summary="Add a child to a family")
def add_child(request, family_id: uuid.UUID, payload: s.AddChildIn):
    """Registers a new child (e.g. a sibling) and signs them up for the current season. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    family = _family(club, family_id)

    def work():
        form = AddChildForm(_blank_none(payload.model_dump()))
        if not form.is_valid():
            raise form_error(form, FIELD_ALIASES)
        add_child_to_family(club, current_season(club), family, **form.cleaned_data)
        return 201, _family_out(request, club, family)

    return idempotent(request, work)


@router.post(PREFIX + "/families/{uuid:family_id}/parents", response={201: s.AddParentOut, **ERRORS, **INVALID}, summary="Add a parent to a family")
def add_parent(request, family_id: uuid.UUID, payload: s.AddParentIn):
    """A new email is added straight away (`added`); an email that already has an account is emailed an
    invite instead (`invited`); an account already in the family changes nothing. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    family = _family(club, family_id)

    def work():
        form = AddParentForm(_blank_none(payload.model_dump()))
        if not form.is_valid():
            raise form_error(form, FIELD_ALIASES)
        parent, invite = add_or_invite_parent(club, current_season(club), family, invited_by=request.user, request=request, **form.cleaned_data)
        result = "added" if parent is not None else "invited" if invite is not None else "already_in_family"
        return 201, {"result": result, "family": _family_out(request, club, family)}

    return idempotent(request, work)


@router.patch(PREFIX + "/families/{uuid:family_id}/members/{uuid:member_id}", response={200: s.FamilyOut, **ERRORS, **INVALID}, summary="Change a person's role in a family")
def update_family_role(request, family_id: uuid.UUID, member_id: uuid.UUID, payload: s.FamilyRolePatch):
    club = panel_club(request)
    require(request, club, can_manage_members)
    family = _family(club, family_id)
    row = get_object_or_404(FamilyMembership, family=family, member_id=member_id)
    row.role = payload.role
    row.save(update_fields=["role"])
    return _family_out(request, club, family)


@router.get(PREFIX + "/{uuid:member_id}", response={200: s.MemberDetailOut, **ERRORS}, summary="One member")
def get_member(request, member_id: uuid.UUID):
    """The person, this season's membership and their families. Management staff, for people they may see."""
    club = panel_club(request)
    return member_detail(request, club, _member(request, club, member_id))


@router.patch(PREFIX + "/{uuid:member_id}", response={200: s.MemberDetailOut, **ERRORS, 409: ErrorOut, **INVALID}, summary="Change a member")
def update_member(request, member_id: uuid.UUID, payload: s.MemberPatch):
    """Only the fields sent change; `membership` edits the current season's membership (created if the
    person has none yet). Person fields are refused (409 `person_locked`) when `person_fields_locked`.
    `status`, `fee_status` and `fee_amount` need a club admin. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    member = _member(request, club, member_id)
    sent = payload.model_fields_set

    locked = member_has_ties_outside_club(member, club)
    person_sent = {name: getattr(payload, name) for name in PERSON_FIELDS if name in sent}
    if person_sent and locked:
        raise ApiError(409, "person_locked", _("This person's details belong to their own account or another club and can't be changed here."))

    form = None
    if person_sent:
        form = MemberForm(_blank_none({**_person_data(member), **{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in person_sent.items()}}), instance=member)
        if not form.is_valid():
            raise form_error(form, FIELD_ALIASES)

    membership_form = None
    if "membership" in sent and payload.membership is not None:
        changes = payload.membership.model_dump(exclude_unset=True)
        admin = is_club_admin(request.user, club)
        if not admin and any(name in changes for name in ADMIN_ONLY_MEMBERSHIP_FIELDS):
            raise ApiError(403, "forbidden", _("Only a club admin can change a membership's status or fees."))
        season = current_season(club)
        if season is None:
            raise ApiError(409, "no_active_season", _("There is no active season to change a membership for."))
        membership = ClubMembership.objects.filter(club=club, member=member, season=season).first() or ClubMembership(club=club, member=member, season=season, signed_up_at=timezone.localdate())
        data = {"kind": membership.kind, "license": membership.license, "status": membership.status, "fee_status": membership.fee_status, "fee_amount": str(membership.fee_amount)} | {k: ("" if v is None else str(v)) for k, v in changes.items()}
        membership_form = ClubMembershipForm(data, instance=membership, can_edit_admin_fields=admin)
        if not membership_form.is_valid():
            raise form_error(membership_form, FIELD_ALIASES)

    with transaction.atomic():
        if form is not None:
            form.save()
        if membership_form is not None:
            membership_form.save()
    member.refresh_from_db()
    return member_detail(request, club, member)


@router.delete(PREFIX + "/{uuid:member_id}", response={200: s.DeleteOut, **ERRORS, 409: ErrorOut}, summary="Delete a member (or remove them from the club)")
def delete_member(request, member_id: uuid.UUID):
    """As the web screen: a record that is this club's alone is deleted (409 `member_in_use` while orders or
    invoices still reference it); a shared one only loses this club's data (`removed_from_club`). Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    member = _member(request, club, member_id)

    if member_has_ties_outside_club(member, club):
        remove_member_from_club(member, club)
        return {"result": "removed_from_club"}

    family_ids = list(families_of_club(club).filter(memberships__member=member).values_list("pk", flat=True))
    try:
        with transaction.atomic():
            member.delete()
            Family.objects.filter(pk__in=family_ids, memberships__isnull=True).delete()
    except ProtectedError:
        raise ApiError(409, "member_in_use", _("This person is still referenced by orders or invoices and can't be deleted.")) from None
    return {"result": "deleted"}


@router.get(PREFIX + "/{uuid:member_id}/memberships", response={200: s.MembershipHistoryOut, **ERRORS}, summary="A member's memberships, newest season first")
def membership_history(request, member_id: uuid.UUID):
    club = panel_club(request)
    member = _member(request, club, member_id)
    rows = ClubMembership.objects.filter(club=club, member=member).select_related("season").order_by("-season__start_date")
    return {"items": [_membership_out(row) for row in rows]}


@router.post(PREFIX + "/{uuid:member_id}/login", response={200: s.MemberDetailOut, **ERRORS, 409: ErrorOut, **INVALID}, summary="Give a login-less member an account")
def grant_member_login(request, member_id: uuid.UUID, payload: s.GrantLoginIn):
    """Creates an account (they set a password via the reset link). 409 `has_login` if they already have one,
    `member_shared` if another club also knows them. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    member = _member(request, club, member_id)
    if member.user_id is not None:
        raise ApiError(409, "has_login", _("This person can already sign in."))
    if member_has_ties_outside_club(member, club):
        raise ApiError(409, "member_shared", _("This person is also known to another club, so a login cannot be set up from here."))
    form = GrantLoginForm({"email": payload.email})
    if not form.is_valid():
        raise form_error(form, FIELD_ALIASES)
    grant_login(member, form.cleaned_data["email"])
    member.refresh_from_db()
    return member_detail(request, club, member)


@router.post(PREFIX + "/{uuid:member_id}/families", response={201: s.MemberDetailOut, **ERRORS, **INVALID}, summary="Link a member into a family")
def attach_family(request, member_id: uuid.UUID, payload: s.AttachFamilyIn):
    """Into an existing family of this club (`family_id`) or a new one (omit it). Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    member = _member(request, club, member_id)
    form = AttachToFamilyForm({"role": payload.role, "family": str(payload.family_id) if payload.family_id else ""}, club=club, member=member)
    if not form.is_valid():
        raise form_error(form, FIELD_ALIASES)
    attach_to_family(member, role=form.cleaned_data["role"], family=form.cleaned_data["family"], club=club)
    return 201, member_detail(request, club, member)


@router.delete(PREFIX + "/{uuid:member_id}/families/{uuid:family_id}", response={200: s.MemberDetailOut, **ERRORS}, summary="Remove a member from a family")
def detach_family(request, member_id: uuid.UUID, family_id: uuid.UUID):
    """The family is deleted when this was its last person. Member admins."""
    club = panel_club(request)
    require(request, club, can_manage_members)
    member = _member(request, club, member_id)
    family = get_object_or_404(families_of_club(club), pk=family_id, memberships__member=member)
    detach_from_family(member, family)
    return member_detail(request, club, member)
