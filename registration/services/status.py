"""The registrant's no-login status page, reached by the batch's secret token: what was registered, the fee (once the invoice is confirmed),
the checklist, and uploading the documents that are asked for. Ported from the old ``RegistrationStatusView``."""

from django.core.files import File
from django.db import transaction

from members.models import ClubMembership, OnboardingRequirement
from members.services import memberships as fees
from members.services import onboarding
from members.services.errors import Invalid
from registration.models import RegistrationBatch, RegistrationDetails

UPLOAD_NOTE = "Uploaded by the registrant via the status page."


def get_batch(token: str) -> RegistrationBatch | None:
    return RegistrationBatch.objects.select_related("season", "contact").filter(status_token=token).first()


def _fee(batch: RegistrationBatch, membership: ClubMembership) -> dict | None:
    """Money details, only once the invoice is confirmed (before that the price is still under review)."""
    if not batch.confirmed_at:
        return None
    fees.sync_fee_status(membership)
    discount = membership.early_payment_discount
    return {
        "fee_amount": membership.fee_amount, "amount_paid": membership.amount_paid, "amount_due": fees.amount_due(membership), "remaining_balance": fees.remaining_balance(membership),
        "fee_status": membership.fee_status,
        "early_bird": {"deadline": membership.early_payment_deadline, "discount": discount, "fee_if_paid_in_time": max(membership.fee_amount - discount, 0), "applies": fees.discount_applies(membership)}
        if membership.early_payment_deadline and discount
        else None,
    }  # fmt: skip


def status_data(batch: RegistrationBatch) -> dict:
    people = []
    for d in batch.details.select_related("member", "membership", "requested_team", "requested_position"):
        items = onboarding.checklist_for(d.membership)
        people.append(
            {
                "member_id": d.member_id, "name": str(d.member), "entry_kind": d.entry_kind, "description": d.description, "requested_team": d.requested_team.name if d.requested_team else None,
                "membership_status": d.membership.status, "is_credit": d.is_credit, "credit": (-d.price if d.is_credit else d.credit_share) if batch.confirmed_at else None, "fee": _fee(batch, d.membership),
                "checklist": [
                    {
                        "requirement_id": i.requirement.pk, "name": i.requirement.name, "description": i.requirement.description, "requires_document": i.requirement.requires_document, "state": i.state,
                        "display": i.display, "valid_until": i.valid_until, "has_document": bool(i.status and i.status.document), "can_upload": i.requirement.requires_document and not i.resolved,
                    }
                    for i in items
                ],
            }
        )  # fmt: skip
    return {"status": batch.status, "season_name": batch.season.name, "submitted_at": batch.submitted_at, "invoice_number": batch.invoice_number, "invoice_due_date": batch.invoice_due_date, "people": people}


@transaction.atomic
def upload_document(batch: RegistrationBatch, requirement_id, member_id, document: File) -> None:
    """The registrant uploads the document of one open requirement for one person of the batch. Refused (``code``): ``cancelled``,
    ``unknown_person`` / ``unknown_requirement`` (404-like, ``Invalid``), ``not_required`` (no document asked for), ``already_resolved``."""
    if batch.cancelled_at:
        raise Invalid("This registration was cancelled.", {"__all__": ["This registration was cancelled."]}, code="cancelled")
    details = list(batch.details.select_related("membership"))
    if member_id is None and len(details) == 1:
        member_id = details[0].member_id
    detail: RegistrationDetails | None = next((d for d in details if d.member_id == member_id), None)
    if detail is None:
        raise Invalid("Choose who the document is for.", {"member_id": ["Unknown person."]}, code="unknown_person")
    requirement = OnboardingRequirement.objects.filter(pk=requirement_id, is_active=True).first()
    if requirement is None:
        raise Invalid("Unknown requirement.", {"requirement_id": ["Unknown requirement."]}, code="unknown_requirement")
    if not requirement.requires_document:
        raise Invalid("No document is needed for this item.", {"requirement_id": ["No document is needed."]}, code="not_required")
    item = next(i for i in onboarding.checklist_for(detail.membership) if i.requirement.pk == requirement.pk)
    if item.resolved:
        raise Invalid("This item is already done.", {"requirement_id": ["Already done."]}, code="already_resolved")
    onboarding._resolve(None, detail.membership, requirement, "onboarding.completed", complete=True, document=document, note=UPLOAD_NOTE)
