"""The sign-up queue (``/signups``): pending season memberships waiting for approval. Everything needs ``members.manage_members``
(403 otherwise); rules live in ``members.services.signups``."""

import uuid

from ninja import Router
from ninja.errors import HttpError

from api.schemas_signups import ApproveAllIn, ApproveAllOut, CancelOut, DuplicatesOut, LinkIn, PlaceIn, PlaceOut, SignupListOut, SignupOptionsOut, SignupOut, SignupSummaryOut
from events.models import Season
from members.models import ClubMembership
from members.services import access, signups
from members.services.errors import Invalid
from registration.services import installments, invoicing
from teams.models import Team

router = Router()


def _gate(request):
    access.require_admin(request.user)


def _season(season_id) -> Season | None:
    if season_id is None:
        return Season.get_current()
    season = Season.objects.filter(pk=season_id).first()
    if season is None:
        raise Invalid("Unknown season.", {"season_id": ["Unknown season."]})
    return season


def _membership(membership_id) -> ClubMembership:
    m = ClubMembership.objects.select_related("member", "season").filter(pk=membership_id).first()
    if m is None:
        raise HttpError(404, "Not found.")
    return m


def _registration(d) -> dict | None:
    if d is None:
        return None
    contact = d.batch.contact
    return {
        "registration_id": d.batch_id, "entry_kind": d.entry_kind, "invoice_number": d.batch.invoice_number, "contact_name": str(contact) if contact else None, "contact_email": contact.email if contact else None,
        "requested_team_id": d.requested_team_id, "requested_team_name": d.requested_team.name if d.requested_team else None, "requested_position_id": d.requested_position_id,
        "requested_position_name": d.requested_position.name if d.requested_position else None, "requested_jersey_number": d.requested_jersey_number, "number_state": invoicing.number_state(d), "product": d.description,
    }  # fmt: skip


def _out(row: dict) -> dict:
    m: ClubMembership = row["membership"]
    return {
        "membership_id": m.pk,
        "member": {"id": m.member_id, "name": m.member.get_full_name(), "email": m.member.email, "date_of_birth": m.member.date_of_birth, "has_login": row["has_login"]},
        "season_id": m.season_id, "season_name": m.season.name, "fee_status": m.fee_status, "fee_amount": m.fee_amount, "amount_paid": m.amount_paid,
        "remaining_balance": row["remaining_balance"], "signed_up_at": m.signed_up_at, "open_requirements": row["open_requirements"], "overdue": installments.overdue(m), "clean": row["clean"],
        "teams": [{"id": t.pk, "name": t.name} for t in row["teams"]], "registration": _registration(row.get("registration")),
    }  # fmt: skip


@router.get("", response=SignupListOut, summary="Pending sign-ups of a season (admin)", operation_id="list_signups")
def list_signups(request, season_id: uuid.UUID | None = None, q: str = "", only: str = "all"):
    """``only``: ``all``, ``clean`` (can be approved now) or ``open`` (fee or requirements outstanding). ``q`` searches name and email."""
    _gate(request)
    season = _season(season_id)
    everything = signups.rows(season, q=q)
    shown = [r for r in everything if only == "all" or (only == "clean") == r["clean"]]
    return {"count": len(shown), "clean_count": sum(1 for r in everything if r["clean"]), "season_id": season.pk if season else None, "season_name": season.name if season else None, "results": [_out(r) for r in shown]}


@router.get("/summary", response=SignupSummaryOut, summary="Counts for the nav badge (admin)", operation_id="signups_summary")
def summary(request, season_id: uuid.UUID | None = None):
    _gate(request)
    season = _season(season_id)
    return {"season_id": season.pk if season else None, **signups.summary(season)}


@router.get("/options", response=SignupOptionsOut, summary="Teams and positions (player and staff) to place a sign-up with (admin)", operation_id="signups_options")
def options(request):
    _gate(request)
    return {"teams": [{"id": t.pk, "name": t.name} for t in Team.objects.order_by("name")], "positions": [{"id": p.pk, "name": p.name, "staff": p.staff_position} for p in signups.positions_for_placement()]}


@router.post("/approve-clean", response=ApproveAllOut, summary="Approve every sign-up that is ready (admin)", operation_id="approve_clean_signups")
def approve_clean(request, payload: ApproveAllIn):
    _gate(request)
    return {"approved": signups.approve_all_clean(request.user, _season(payload.season_id))}


@router.post("/{membership_id}/approve", response=SignupOut, summary="Approve one sign-up (admin)", operation_id="approve_signup")
def approve(request, membership_id: uuid.UUID):
    """422 when the payment is not current or requirements are still open (``errors.requirements`` names them)."""
    _gate(request)
    membership = signups.approve(request.user, _membership(membership_id))
    return _out(signups.describe(membership))


@router.post("/{membership_id}/place", response={201: PlaceOut}, summary="Put a sign-up on a team (admin)", operation_id="place_signup")
def place(request, membership_id: uuid.UUID, payload: PlaceIn):
    _gate(request)
    team = Team.objects.filter(pk=payload.team_id).first()
    if team is None:
        raise Invalid("Unknown team.", {"team_id": ["Unknown team."]})
    spot = signups.place_in_team(request.user, _membership(membership_id), team, position_id=payload.position_id, jersey_number=payload.jersey_number)
    return 201, {"team": {"id": team.pk, "name": team.name}, "position": spot.position.name if spot.position_id else None, "jersey_number": getattr(spot, "jersey_number", None)}


@router.post("/{membership_id}/cancel", response=CancelOut, summary="Turn down a sign-up (admin)", operation_id="cancel_signup")
def cancel(request, membership_id: uuid.UUID):
    """A person who is nothing but this unpaid sign-up is removed; anyone else keeps their record and the membership is cancelled."""
    _gate(request)
    return {"result": signups.cancel(request.user, _membership(membership_id))}


@router.get("/{membership_id}/duplicates", response=DuplicatesOut, summary="People who may be the same as this sign-up (admin)", operation_id="signup_duplicates")
def duplicates(request, membership_id: uuid.UUID):
    """Same email, or same name (and birthday when both are known). Public registrations never match an existing person by themselves."""
    from registration.services import linking

    _gate(request)
    rows = linking.possible_duplicates(_membership(membership_id))
    return {"results": [{"member_id": r["member"].pk, "name": r["member"].get_full_name(), "email": r["member"].email, "date_of_birth": r["member"].date_of_birth, "same_email": r["same_email"], "has_membership": r["has_membership"]} for r in rows]}


@router.post("/{membership_id}/link", response=SignupOut, summary="Merge this sign-up into an existing member (admin)", operation_id="link_signup")
def link(request, membership_id: uuid.UUID, payload: LinkIn):
    """The pending membership, family links and roster spots move to the existing member; the duplicate person is removed. 422 ``already_member``
    when they already have a membership this season."""
    from members.models import Member
    from registration.services import linking

    _gate(request)
    existing = Member.objects.filter(pk=payload.member_id).first()
    if existing is None:
        raise Invalid("Unknown member.", {"member_id": ["Unknown member."]})
    membership = linking.link_to_existing_member(request.user, _membership(membership_id), existing)
    return _out(signups.describe(ClubMembership.objects.select_related("member", "season").get(pk=membership.pk)))
