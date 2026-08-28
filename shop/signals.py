"""Bust an order's cached invoice PDF the moment its payment_status or
fulfillment_status actually changes -- the PDF's paid/owed styling and
status line are derived from both (see shop/templates/shop/
order_invoice_pdf.html), so a stale cached copy would otherwise keep
showing a status the order no longer has.

Also seeds and protects the two system ProductCategory rows (Player/
Volunteer) registration.forms.RegistrationEntryRowForm.clean checks a
chosen product against -- see ProductCategory's own docstring.

Registered from ShopConfig.ready.
"""

from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from club.models import Club

from .models import Invoice, Order, ProductCategory
from .services.invoices import invalidate_cached_invoice_pdf


@receiver(pre_save, sender=Order)
def invalidate_invoice_pdf_on_status_change(sender, instance, **kwargs):
    if not instance.pk:
        return

    previous = Order.objects.filter(pk=instance.pk).values("payment_status", "fulfillment_status").first()
    if previous is None:
        return
    if previous["payment_status"] == instance.payment_status and previous["fulfillment_status"] == instance.fulfillment_status:
        return

    invoice = Invoice.objects.filter(order_id=instance.pk).first()
    if invoice is not None:
        invalidate_cached_invoice_pdf(invoice)


class ProtectedCategoryError(Exception):
    """Raised when something tries to delete one of the two system
    registration categories -- management.views.ProductCategoryDeleteView
    checks registration_kind itself first and never lets a request reach
    this, so hitting it means some other code path tried to."""


#: Mirrors ProductCategory.RegistrationKind -- (kind, display name) pairs,
#: capitalised per the club's own convention for these two.
_REGISTRATION_CATEGORIES = ((ProductCategory.RegistrationKind.PLAYER, _("Player")), (ProductCategory.RegistrationKind.VOLUNTEER, _("Volunteer")))


def ensure_registration_categories(club):
    """Idempotent -- safe to call for a club that already has its two
    system categories (create_registration_categories below only fires
    once, at Club creation, but this is also what shop/migrations/
    0027_seed_registration_categories.py's live-code equivalent would do
    for a club created before that migration ran)."""
    for kind, name in _REGISTRATION_CATEGORIES:
        category, created = ProductCategory.objects.get_or_create(club=club, name=name, defaults={"registration_kind": kind})
        if not created and not category.registration_kind:
            category.registration_kind = kind
            category.save(update_fields=["registration_kind"])


@receiver(post_save, sender=Club)
def create_registration_categories(sender, instance, created, **kwargs):
    if created:
        ensure_registration_categories(instance)


@receiver(pre_delete, sender=ProductCategory)
def prevent_deleting_system_category(sender, instance, origin=None, **kwargs):
    """Only blocks a delete aimed at this category itself (management.views.
    ProductCategoryDeleteView's own .delete() call, or a queryset delete
    targeting ProductCategory directly) -- ``origin`` is whatever .delete()
    was originally called on (see Collector.__init__), so a category swept
    up as part of deleting its whole Club (a legitimate full-tenant wipe,
    club.tests.ClubMembershipModelTests) is left alone."""
    if not instance.registration_kind:
        return
    is_direct_delete = origin is instance or getattr(origin, "model", None) is ProductCategory
    if is_direct_delete:
        raise ProtectedCategoryError(f"“{instance.name}” is a system registration category and can't be deleted.")
