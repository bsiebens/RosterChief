"""Who may download a registration invoice as a member (``/portal/invoices/{id}.pdf`` and the invoice links in the profile).

Only (1) the registration's contact, and (2) a parent or guardian (``FamilyMembership.GUARDIAN_ROLES``) of a family that contains one of the
people the invoice is for. Not children, not other adults in the family (roles ``other``...), not other members."""

from django.db.models import Exists, OuterRef, Q

from members.models import FamilyMembership, Member
from registration.models import RegistrationBatch, RegistrationDetails


def downloadable_invoices(viewer: Member):
    """The confirmed, invoiced, not cancelled registrations ``viewer`` may download the invoice of."""
    covered = RegistrationDetails.objects.filter(batch=OuterRef("pk"), member__family_memberships__family__memberships__member=viewer, member__family_memberships__family__memberships__role__in=FamilyMembership.GUARDIAN_ROLES)
    return RegistrationBatch.objects.filter(confirmed_at__isnull=False, cancelled_at__isnull=True, invoice_number__isnull=False).filter(Q(contact=viewer) | Exists(covered))


def can_download(viewer: Member, batch: RegistrationBatch) -> bool:
    return downloadable_invoices(viewer).filter(pk=batch.pk).exists()
