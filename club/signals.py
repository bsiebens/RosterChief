"""Keep ClubRole in sync with membership status, and bust a membership's
cached dues invoice PDF the moment its fee_status actually changes.

An **active** ClubMembership grants the member a ``MEMBER`` ClubRole; when no
active membership remains in that club (status changed away from active, or the
membership was deleted), the ``MEMBER`` role is withdrawn.

A member holds at most one ClubRole per club (``unique_member_per_club``), so an
elevated role (ADMIN / EDITOR) is never downgraded or removed by this sync — it
simply takes precedence.

Separately: DuesInvoice's PDF (club/templates/club/dues_invoice_pdf.html) renders
paid/owed off the membership's own live fee_status, not anything stored on the
invoice -- see club.services.invoicing.invoice_pdf's own caching. A cached copy
from before a payment was recorded would keep showing "owed" forever otherwise.
"""

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import ClubMembership, ClubRole, DuesInvoice
from .services.invoicing import invalidate_cached_invoice_pdf


@receiver(post_save, sender=ClubMembership)
@receiver(post_delete, sender=ClubMembership)
def sync_member_role(sender, instance, **kwargs):
    has_active = ClubMembership.objects.filter(
        club_id=instance.club_id,
        member_id=instance.member_id,
        status=ClubMembership.StatusChoices.ACTIVE,
    ).exists()

    if has_active:
        # get_or_create keeps an existing ADMIN/EDITOR role untouched.
        ClubRole.objects.get_or_create(
            club_id=instance.club_id,
            member_id=instance.member_id,
            defaults={"role": ClubRole.Roles.MEMBER},
        )
    else:
        ClubRole.objects.filter(
            club_id=instance.club_id,
            member_id=instance.member_id,
            role=ClubRole.Roles.MEMBER,
        ).delete()


@receiver(pre_save, sender=ClubMembership)
def invalidate_dues_pdf_on_fee_status_change(sender, instance, **kwargs):
    if not instance.pk:
        return

    previous_fee_status = ClubMembership.objects.filter(pk=instance.pk).values_list("fee_status", flat=True).first()
    if previous_fee_status is None or previous_fee_status == instance.fee_status:
        return

    invoice = DuesInvoice.objects.filter(membership_id=instance.pk).first()
    if invoice is not None:
        invalidate_cached_invoice_pdf(invoice)
