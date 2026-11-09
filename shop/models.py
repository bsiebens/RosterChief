"""Club shop (one schema per club, so no club FK anywhere). Ported from the old site's ``shop`` app minus carts,
registration pricing (registrant tiers, early bird, season, staff role) which v2 does not have yet."""

import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import Q, UniqueConstraint
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.text import get_valid_filename, slugify
from django.utils.translation import gettext_lazy as _

from members.models import Member
from rosterchief.base import UUIDModel
from teams.models import Team


class DiscountType(models.TextChoices):
    """Shared by ``Discount`` and its snapshot on an order (``AppliedDiscount``)."""

    PERCENTAGE = "percentage", _("Percentage")
    FIXED_AMOUNT = "fixed_amount", _("Fixed amount")


class ProductionStatus(models.TextChoices):
    """Merchandise order lines: to order -> ordered from the supplier (in a ProductionBatch) -> received. ``Order`` rolls its
    lines up into the same three values."""

    PENDING = "pending", _("To order")
    ORDERED = "ordered", _("Ordered from supplier")
    RECEIVED = "received", _("Received")


def product_image_path(instance, filename):
    return f"shop/products/{instance.pk}/{filename}"


def product_gallery_path(instance, filename):
    return f"shop/products/{instance.product_id}/images/{filename}"


def product_attachment_path(instance, filename):
    """A random prefix keeps two uploads with the same name apart; the original name is kept (made filesystem-safe) for recognition."""
    return f"shop/products/{instance.product_id}/files/{secrets.token_hex(6)}-{get_valid_filename(filename)}"[:255]


def next_number(kind: str) -> str:
    """The next number for a document type from the shop's numbering patterns (``shop.services.numbering``); needs the creating transaction."""
    from shop.services import numbering

    return numbering.allocate(kind)


def save_with_number(instance, save):
    """Assign the next number and save, both in one transaction (a failed save burns no number), retrying on a collision; relies on
    the model's ``generate_number()`` and the unique constraint on ``number``."""
    if instance.number:
        return save()
    for attempt in range(5):
        try:
            with transaction.atomic():
                instance.number = instance.generate_number()
                return save()
        except IntegrityError:
            instance.number = ""
            if attempt == 4:
                raise


def unique_slug(instance, value: str) -> str:
    """A slug from ``value`` no other row of the model has: ``-2``, ``-3``, ... on collision."""
    max_length = instance._meta.get_field("slug").max_length
    base = slugify(value)[:max_length] or "item"
    others = type(instance).objects.exclude(pk=instance.pk)
    slug, suffix = base, 2
    while others.filter(slug=slug).exists():
        tail = f"-{suffix}"
        slug = f"{base[: max_length - len(tail)]}{tail}"
        suffix += 1
    return slug


class ProductCategory(UUIDModel):
    name = models.CharField(_("name"), max_length=255)

    class Meta:
        verbose_name = _("product category")
        verbose_name_plural = _("product categories")
        ordering = ["name"]
        constraints = [UniqueConstraint(Lower("name"), name="unique_shop_category_name")]

    def __str__(self):
        return self.name


class Product(UUIDModel):
    class ProductType(models.TextChoices):
        MEMBERSHIP = "membership", _("Membership")
        EVENT_FEE = "event_fee", _("Event fee")
        MERCHANDISE = "merchandise", _("Merchandise")

    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=255, unique=True, blank=True)
    category = models.ForeignKey(ProductCategory, on_delete=models.SET_NULL, related_name="products", verbose_name=_("category"), blank=True, null=True)
    description = models.TextField(_("description"), blank=True)
    price = models.DecimalField(_("price"), max_digits=10, decimal_places=2, default=0)
    product_type = models.CharField(_("product type"), max_length=255, choices=ProductType.choices, default=ProductType.MERCHANDISE)

    is_active = models.BooleanField(_("is active?"), default=True)
    is_public = models.BooleanField(_("is public?"), default=True, help_text=_("Non-public products are only visible to staff for adding to an order."))
    personalization_enabled = models.BooleanField(_("personalization enabled?"), default=False)
    #: Bought in bulk and counted (InventoryMovement) instead of made to order: orders draw down stock and skip production.
    inventory_managed = models.BooleanField(_("inventory managed?"), default=False)

    class Meta:
        verbose_name = _("product")
        verbose_name_plural = _("products")
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def main_image(self):
        """The picture shown in lists: the main one, else the first. Reads ``images.all()`` so a prefetch is honoured."""
        images = list(self.images.all())
        return next((img for img in images if img.is_main), None) or (images[0] if images else None)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name)
        super().save(*args, **kwargs)

    def clean(self):
        if self.inventory_managed and self.product_type != self.ProductType.MERCHANDISE:
            raise ValidationError({"inventory_managed": _("Only merchandise can be inventory managed.")})


class ProductImage(UUIDModel):
    """One picture of a product's gallery; exactly one is the main picture (the one shown in lists), the rest follow in ``ordering``."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images", verbose_name=_("product"))
    image = models.ImageField(_("image"), upload_to=product_gallery_path, max_length=255)
    alt = models.CharField(_("description"), max_length=255, blank=True)
    is_main = models.BooleanField(_("main picture"), default=False)
    ordering = models.PositiveSmallIntegerField(_("ordering"), default=0)

    class Meta:
        verbose_name = _("product image")
        verbose_name_plural = _("product images")
        ordering = ["ordering", "created"]
        constraints = [UniqueConstraint(fields=["product"], condition=Q(is_main=True), name="unique_main_image_per_product")]

    def __str__(self):
        return f"{self.product} - picture"


class ProductAttachment(UUIDModel):
    """A file belonging to a product, e.g. a size guide: served from the same public media as the pictures."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="attachments", verbose_name=_("product"))
    file = models.FileField(_("file"), upload_to=product_attachment_path, max_length=255)
    title = models.CharField(_("title"), max_length=255)
    name = models.CharField(_("file name"), max_length=255, blank=True)
    size = models.PositiveIntegerField(_("size (bytes)"), default=0)
    content_type = models.CharField(_("content type"), max_length=100, blank=True)

    class Meta:
        verbose_name = _("product attachment")
        verbose_name_plural = _("product attachments")
        ordering = ["created"]

    def __str__(self):
        return self.title


class ProductVariant(UUIDModel):
    """One orderable option of a product ("Small", "Medium - Red"): a single free-text label."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants", verbose_name=_("product"))
    name = models.CharField(_("name"), max_length=255)
    price = models.DecimalField(_("price"), max_digits=10, decimal_places=2, blank=True, null=True, help_text=_("Leave blank to use the product's own price."))
    is_active = models.BooleanField(_("is active?"), default=True)
    ordering = models.PositiveSmallIntegerField(_("ordering"), default=0)

    class Meta:
        verbose_name = _("product variant")
        verbose_name_plural = _("product variants")
        ordering = ["ordering", "name"]
        constraints = [UniqueConstraint(fields=["product", "name"], name="unique_variant_name_per_product")]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return self.product.name if self.name == self.product.name else f"{self.product.name} - {self.name}"

    @property
    def effective_price(self):
        return self.price if self.price is not None else self.product.price


class Order(UUIDModel):
    class PaymentStatus(models.TextChoices):
        PENDING = "pending", _("Pending")
        PARTIALLY_PAID = "partially_paid", _("Partially paid")
        PAID = "paid", _("Paid")
        REFUNDED = "refunded", _("Refunded")

    class FulfillmentStatus(models.TextChoices):
        NOT_READY = "not_ready", _("Not ready")
        READY_FOR_PICKUP = "ready_for_pickup", _("Ready for pickup")
        DELIVERED = "delivered", _("Delivered")
        CANCELLED = "cancelled", _("Cancelled")

    number = models.CharField(_("number"), max_length=255, blank=True)
    purchaser = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="shop_orders", verbose_name=_("purchaser"))
    payment_status = models.CharField(_("payment status"), max_length=255, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    fulfillment_status = models.CharField(_("fulfillment status"), max_length=255, choices=FulfillmentStatus.choices, default=FulfillmentStatus.NOT_READY)
    #: Rolled up from the merchandise lines (PENDING while all are, RECEIVED once all are, ORDERED in between).
    production_status = models.CharField(_("production status"), max_length=255, choices=ProductionStatus.choices, default=ProductionStatus.PENDING)
    total = models.DecimalField(_("total"), max_digits=10, decimal_places=2)
    pickup_instructions = models.TextField(_("pickup instructions"), blank=True)
    notes = models.TextField(_("notes"), blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="+", verbose_name=_("created by"), blank=True, null=True)

    class Meta:
        verbose_name = _("order")
        verbose_name_plural = _("orders")
        ordering = ["-created"]
        constraints = [UniqueConstraint(fields=["number"], name="unique_shop_order_number")]

    def __str__(self):
        return self.number

    @property
    def is_closed(self) -> bool:
        """Delivered and paid: genuinely finished."""
        return self.fulfillment_status == self.FulfillmentStatus.DELIVERED and self.payment_status == self.PaymentStatus.PAID

    @property
    def has_production_lines(self) -> bool:
        return self.lines.filter(product__product_type=Product.ProductType.MERCHANDISE, product__inventory_managed=False).exists()

    def generate_number(self):
        return next_number("order")

    def save(self, *args, **kwargs):
        return save_with_number(self, lambda: super(Order, self).save(*args, **kwargs))


class ProductionBatch(UUIDModel):
    """A group of order lines sent to a supplier in one go (exported as CSV)."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        ORDERED = "ordered", _("Ordered")
        PARTIALLY_RECEIVED = "partially_received", _("Partially received")
        RECEIVED = "received", _("Received")

    number = models.CharField(_("number"), max_length=255, blank=True)
    reference = models.CharField(_("reference"), max_length=255, blank=True)
    supplier = models.CharField(_("supplier"), max_length=255, blank=True)
    note = models.TextField(_("note"), blank=True)
    status = models.CharField(_("status"), max_length=30, choices=Status.choices, default=Status.ORDERED)
    ordered_at = models.DateTimeField(_("ordered at"), default=timezone.now)
    received_at = models.DateTimeField(_("received at"), blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="+", verbose_name=_("created by"), blank=True, null=True)

    class Meta:
        verbose_name = _("production batch")
        verbose_name_plural = _("production batches")
        ordering = ["-ordered_at"]
        constraints = [UniqueConstraint(fields=["number"], name="unique_shop_batch_number")]

    def __str__(self):
        return self.number

    def generate_number(self):
        return next_number("batch")

    def save(self, *args, **kwargs):
        return save_with_number(self, lambda: super(ProductionBatch, self).save(*args, **kwargs))


class OrderLine(UUIDModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines", verbose_name=_("order"))
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_lines", verbose_name=_("product"))
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="order_lines", verbose_name=_("variant"), blank=True, null=True)

    quantity = models.PositiveSmallIntegerField(_("quantity"), default=1)
    unit_price = models.DecimalField(_("unit price"), max_digits=10, decimal_places=2)
    line_total = models.DecimalField(_("line total"), max_digits=10, decimal_places=2)

    beneficiary = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="shop_order_lines", verbose_name=_("beneficiary"), blank=True, null=True)
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="shop_order_lines", verbose_name=_("team"), blank=True, null=True)
    personalization_number = models.CharField(_("number"), max_length=20, blank=True)
    personalization_name = models.CharField(_("name"), max_length=100, blank=True)

    production_status = models.CharField(_("production status"), max_length=255, choices=ProductionStatus.choices, default=ProductionStatus.PENDING)
    production_batch = models.ForeignKey(ProductionBatch, on_delete=models.SET_NULL, related_name="lines", verbose_name=_("production batch"), blank=True, null=True)
    #: Units received so far from the supplier (partial deliveries); the line is RECEIVED once it reaches ``quantity``.
    received_quantity = models.PositiveSmallIntegerField(_("received quantity"), default=0)
    ordered_at = models.DateTimeField(_("ordered at"), blank=True, null=True)
    received_at = models.DateTimeField(_("received at"), blank=True, null=True)

    class Meta:
        verbose_name = _("order line")
        verbose_name_plural = _("order lines")
        ordering = ["created"]

    def __str__(self):
        suffix = f" ({self.variant.name})" if self.variant_id else ""
        return f"{self.product}{suffix} - {self.quantity}x"

    def clean(self):
        if self.variant_id and self.product_id and self.variant.product_id != self.product_id:
            raise ValidationError({"variant": _("Must be a variant of this product.")})


class InventoryMovement(UUIDModel):
    """One signed change to the stock of an inventory-managed product/variant; on hand = sum of ``quantity``."""

    class Reason(models.TextChoices):
        RECEIVED = "received", _("Stock taken in")
        ADJUSTMENT = "adjustment", _("Count corrected")
        ORDER = "order", _("Ordered")
        ORDER_RETURNED = "order_returned", _("Order cancelled or changed")

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="inventory_movements", verbose_name=_("product"))
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="inventory_movements", verbose_name=_("variant"), blank=True, null=True)
    quantity = models.IntegerField(_("quantity"))
    reason = models.CharField(_("reason"), max_length=20, choices=Reason.choices)
    note = models.CharField(_("note"), max_length=255, blank=True)
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, related_name="inventory_movements", verbose_name=_("order"), blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="+", verbose_name=_("created by"), blank=True, null=True)

    class Meta:
        verbose_name = _("inventory movement")
        verbose_name_plural = _("inventory movements")
        ordering = ["-created"]
        indexes = [models.Index(fields=["product", "variant"])]

    def __str__(self):
        suffix = f" ({self.variant.name})" if self.variant_id else ""
        return f"{self.product}{suffix}: {self.quantity:+d}"


class Discount(UUIDModel):
    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=255, unique=True, blank=True)
    description = models.TextField(_("description"), blank=True)
    #: Always stored uppercased; compared case-insensitively.
    code = models.CharField(_("code"), max_length=50, unique=True)
    discount_type = models.CharField(_("discount type"), max_length=255, choices=DiscountType.choices, default=DiscountType.PERCENTAGE)
    discount_amount = models.DecimalField(_("discount amount"), max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(_("is active?"), default=True)
    valid_from = models.DateField(_("valid from"), blank=True, null=True)
    valid_until = models.DateField(_("valid until"), blank=True, null=True)
    max_uses = models.PositiveIntegerField(_("maximum uses"), blank=True, null=True)
    once_per_member = models.BooleanField(_("once per member?"), default=True)

    class Meta:
        verbose_name = _("order discount")
        verbose_name_plural = _("order discounts")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValidationError({"valid_until": _("Must be on or after the first valid day.")})

    def save(self, *args, **kwargs):
        self.code = self.code.upper().strip()
        if not self.slug:
            self.slug = unique_slug(self, self.name)
        super().save(*args, **kwargs)

    def is_within_validity(self, today=None) -> bool:
        today = today or timezone.localdate()
        return (self.valid_from is None or self.valid_from <= today) and (self.valid_until is None or today <= self.valid_until)


class AppliedDiscount(UUIDModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="applied_discounts", verbose_name=_("order"))
    discount = models.ForeignKey(Discount, on_delete=models.PROTECT, related_name="applied_discounts", verbose_name=_("discount"))
    #: Snapshot of the terms when applied: for a percentage ``discount_amount`` is the rate, ``amount`` the money deducted.
    discount_type = models.CharField(_("discount type"), max_length=255, choices=DiscountType.choices, default=DiscountType.PERCENTAGE)
    discount_amount = models.DecimalField(_("discount amount"), max_digits=10, decimal_places=2)
    amount = models.DecimalField(_("amount deducted"), max_digits=10, decimal_places=2, default=0)
    description = models.TextField(_("description"), blank=True)
    applied_by = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="shop_applied_discounts", verbose_name=_("applied by"), blank=True, null=True)

    class Meta:
        verbose_name = _("applied discount")
        verbose_name_plural = _("applied discounts")
        constraints = [UniqueConstraint(fields=["order", "discount"], name="unique_discount_per_order")]

    def __str__(self):
        suffix = "%" if self.discount_type == DiscountType.PERCENTAGE else ""
        return f"{self.discount} - {self.discount_amount}{suffix}"


class Voucher(UUIDModel):
    """Prepaid credit, redeemable in parts against orders (Payment.voucher) or manually (VoucherConsumption).
    ``consumed_amount`` is only changed by the voucher services, under a row lock."""

    number = models.CharField(_("number"), max_length=255, blank=True)
    issued_to = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="shop_vouchers", verbose_name=_("issued to"), blank=True, null=True)
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2)
    consumed_amount = models.DecimalField(_("consumed amount"), max_digits=10, decimal_places=2, default=0)
    expiry_date = models.DateField(_("expiry date"))
    is_active = models.BooleanField(_("is active?"), default=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("voucher")
        verbose_name_plural = _("vouchers")
        ordering = ["-created"]
        constraints = [UniqueConstraint(fields=["number"], name="unique_shop_voucher_number")]

    def __str__(self):
        return self.number

    def clean(self):
        if self.consumed_amount > self.amount:
            raise ValidationError({"consumed_amount": _("Cannot exceed the voucher's own amount.")})

    @property
    def available_amount(self):
        return self.amount - self.consumed_amount

    @property
    def is_expired(self):
        return self.expiry_date < timezone.localdate()

    @property
    def is_usable(self):
        return self.is_active and not self.is_expired and self.available_amount > 0

    def generate_number(self):
        return next_number("voucher")

    def save(self, *args, **kwargs):
        return save_with_number(self, lambda: super(Voucher, self).save(*args, **kwargs))


class VoucherConsumption(UUIDModel):
    """A manual record of a voucher being (partly) used outside an order."""

    voucher = models.ForeignKey(Voucher, on_delete=models.CASCADE, related_name="consumptions", verbose_name=_("voucher"))
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2)
    note = models.TextField(_("note"), blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="+", blank=True, null=True, verbose_name=_("recorded by"))
    recorded_at = models.DateTimeField(_("recorded at"), default=timezone.now)

    class Meta:
        verbose_name = _("voucher consumption")
        verbose_name_plural = _("voucher consumptions")
        ordering = ["-recorded_at"]

    def __str__(self):
        return f"{self.voucher} - {self.amount}"


class Payment(UUIDModel):
    class PaymentMethod(models.TextChoices):
        CREDIT_CARD = "credit_card", _("Credit card")
        BANK_TRANSFER = "bank_transfer", _("Bank transfer")
        CASH = "cash", _("Cash")
        VOUCHER = "voucher", _("Voucher")
        ONLINE = "online", _("Online")

    class PaymentStatus(models.TextChoices):
        PENDING = "pending", _("Pending")
        CONFIRMED = "confirmed", _("Confirmed")
        FAILED = "failed", _("Failed")
        REFUNDED = "refunded", _("Refunded")

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments", verbose_name=_("order"))
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2)
    method = models.CharField(_("method"), max_length=255, choices=PaymentMethod.choices, default=PaymentMethod.BANK_TRANSFER)
    status = models.CharField(_("status"), max_length=255, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    reference = models.CharField(_("reference"), max_length=255, blank=True)
    paid_at = models.DateTimeField(_("paid at"), blank=True, null=True)
    voucher = models.ForeignKey(Voucher, on_delete=models.PROTECT, related_name="payments", verbose_name=_("voucher"), blank=True, null=True)
    #: Online payments through a provider (see the payment-provider abstraction).
    provider = models.CharField(_("provider"), max_length=50, blank=True)
    provider_payment_id = models.CharField(_("provider payment id"), max_length=255, blank=True)
    payment_url = models.URLField(_("payment url"), max_length=1000, blank=True)
    provider_data = models.JSONField(_("provider data"), blank=True, null=True)
    note = models.CharField(_("note"), max_length=255, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="+", verbose_name=_("recorded by"), blank=True, null=True)

    class Meta:
        verbose_name = _("payment")
        verbose_name_plural = _("payments")
        ordering = ["created"]

    def __str__(self):
        return f"{self.order} - {self.status}"

    def clean(self):
        if self.method == self.PaymentMethod.VOUCHER and not self.voucher_id:
            raise ValidationError({"voucher": _("Required when the method is voucher.")})
        if self.method != self.PaymentMethod.VOUCHER and self.voucher_id:
            raise ValidationError({"voucher": _("Only allowed when the method is voucher.")})


class Invoice(UUIDModel):
    number = models.CharField(_("number"), max_length=255, blank=True)
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="invoice", verbose_name=_("order"))
    issued_at = models.DateTimeField(_("issued at"), auto_now_add=True)
    due_date = models.DateField(_("due date"), blank=True, null=True)
    billing_snapshot = models.JSONField(_("billing snapshot"), blank=True, null=True)

    class Meta:
        verbose_name = _("invoice")
        verbose_name_plural = _("invoices")
        ordering = ["-issued_at"]
        constraints = [UniqueConstraint(fields=["number"], name="unique_shop_invoice_number")]

    def __str__(self):
        return self.number

    def generate_number(self):
        return next_number("invoice")

    def save(self, *args, **kwargs):
        return save_with_number(self, lambda: super(Invoice, self).save(*args, **kwargs))
