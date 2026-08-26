from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import Q, UniqueConstraint
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from authentication.models import User
from club.models import Season
from club.tenancy import require_current_club
from members.models import Member
from rosterchief.base import ClubScopedModel, UUIDModel, validate_club_scope
from teams.models import Position, Team


class DiscountType(models.TextChoices):
    """Shared by ``Product`` (early-bird), ``Discount`` and ``AppliedDiscount`` (its
    snapshot on an order) — one discount vocabulary, not three copies of it."""

    PERCENTAGE = "percentage", _("Percentage")
    FIXED_AMOUNT = "fixed_amount", _("Fixed amount")


def product_image_path(instance, filename):
    return f"clubs/{instance.club.slug}/shop/products/{filename}"


def next_scoped_number(instance, code):
    """Next per-club sequential number for the current year: ``<code>-<year>-<seq>``."""
    prefix = f"{code}-{timezone.now().year}-"
    model = type(instance)
    sequences = [int(suffix) for number in model.objects.filter(club=instance.club, number__startswith=prefix).values_list("number", flat=True) if (suffix := number.removeprefix(prefix)).isdigit()]
    return f"{prefix}{max(sequences, default=0) + 1:05d}"


def save_with_number(instance, save):
    """Assign a unique per-club number (retrying on collision) then save.

    Relies on the model's ``generate_number()`` and its ``(club, number)``
    unique constraint as the source of truth.
    """
    if instance.club_id is None:
        instance.club = require_current_club()
    if instance.number:
        return save()
    for attempt in range(5):
        instance.number = instance.generate_number()
        try:
            with transaction.atomic():
                return save()
        except IntegrityError:
            if attempt == 4:
                raise


class ProductCategory(ClubScopedModel):
    """A club-defined grouping for the shop's product grid ("Merch", "Fees",
    "Season tickets", ...) -- purely organisational, no behaviour of its own.
    Deleting one un-categorises its products (SET_NULL on Product.category)
    rather than blocking the delete or taking products down with it."""

    name = models.CharField(_("name"), max_length=255)

    class Meta:
        verbose_name = _("product category")
        verbose_name_plural = _("product categories")
        ordering = ["name"]
        constraints = [UniqueConstraint(fields=["club", "name"], name="unique_category_name_per_club")]

    def __str__(self):
        return self.name


class Product(ClubScopedModel):
    class ProductType(models.TextChoices):
        MEMBERSHIP = "membership", _("Membership")
        EVENT_FEE = "event_fee", _("Event fee")
        MERCHANDISE = "merchandise", _("Merchandise")
        DONATION = "donation", _("Donation")

    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=255, blank=True)
    category = models.ForeignKey(ProductCategory, on_delete=models.SET_NULL, related_name="products", verbose_name=_("category"), blank=True, null=True)
    description = models.TextField(_("description"), blank=True)
    image = models.ImageField(_("photo"), upload_to=product_image_path, blank=True)
    price = models.DecimalField(_("price"), max_digits=10, decimal_places=2, default=0)

    product_type = models.CharField(_("product type"), max_length=255, choices=ProductType.choices, default=ProductType.MEMBERSHIP)
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="products", verbose_name=_("season"), blank=True, null=True)

    is_active = models.BooleanField(_("is active?"), default=True)
    is_public = models.BooleanField(_("is public?"), default=True, help_text=_("Non-public products are only visible to staff members for adding on to an order later on."))
    staff_role = models.ForeignKey(Position, on_delete=models.PROTECT, related_name="staff_products", verbose_name=_("staff role"), blank=True, null=True, limit_choices_to={"staff_position": True})

    #: Off by default -- most products (dues, event fees, plain merch) have
    #: nothing to personalize. Team gear (jerseys, warm-ups, ...) is the case
    #: this exists for: a member picks it and optionally fills in a number
    #: and/or the name to print on it (CartItem/OrderLine's own
    #: personalization_number/personalization_name), carried through to the
    #: manufacturer order export (management.shop_export).
    personalization_enabled = models.BooleanField(_("personalization enabled?"), default=False, help_text=_("Lets a member enter a number and/or a name to print on this item when ordering, e.g. for a jersey."))

    early_bird_discount_enabled = models.BooleanField(_("early bird discount enabled?"), default=False)
    early_bird_discount_deadline = models.DateField(_("early bird discount deadline"), blank=True, null=True)
    early_bird_discount_type = models.CharField(_("early bird discount type"), max_length=255, choices=DiscountType.choices, default=DiscountType.PERCENTAGE)
    early_bird_discount_amount = models.DecimalField(_("early bird discount amount"), max_digits=10, decimal_places=2, default=0)

    slug_source = "name"

    class Meta:
        verbose_name = _("product")
        verbose_name_plural = _("products")
        ordering = ["name"]
        constraints = [
            UniqueConstraint(fields=["club", "slug"], name="unique_product_slug_per_club"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        validate_club_scope(self, self.club_id, same_club_fields=("season", "staff_role", "category"))


class ProductVariant(UUIDModel):
    """One orderable option of a Product -- "Small", "Medium — Red", whatever
    label the club actually sells by. Deliberately a single free-text label
    rather than separate size/colour axes with a generated combination
    matrix: a club merch shop needs "the same jersey in different sizes",
    not a full options/variants system, and a label an admin types once
    covers "size", "colour", or "size and colour together" equally well
    without extra UI for axes nobody asked for.

    No club FK of its own -- scoped via ``product.club``, same shape as
    CartItem/OrderLine below (plain UUIDModel, club implied by a parent FK).
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants", verbose_name=_("product"))
    name = models.CharField(_("name"), max_length=255, help_text=_("e.g. “Small”, “Medium — Red”, “XL”."))
    #: Overrides Product.price when set; leave blank when every variant of a
    #: product costs the same (most of the time) so there's nothing to keep
    #: in sync with the product's own price.
    price = models.DecimalField(_("price"), max_digits=10, decimal_places=2, blank=True, null=True, help_text=_("Leave blank to use the product's own price."))
    is_active = models.BooleanField(_("is active?"), default=True)
    ordering = models.PositiveSmallIntegerField(_("ordering"), default=0)

    class Meta:
        verbose_name = _("product variant")
        verbose_name_plural = _("product variants")
        ordering = ["ordering", "name"]
        constraints = [
            UniqueConstraint(fields=["product", "name"], name="unique_variant_name_per_product"),
        ]

    def __str__(self):
        return f"{self.product} — {self.name}"

    @property
    def effective_price(self):
        return self.price if self.price is not None else self.product.price


class Cart(ClubScopedModel):
    class CartStatus(models.TextChoices):
        OPEN = "open", _("Open")
        CHECKED_OUT = "checked_out", _("Checked out")
        ABANDONED = "abandoned", _("Abandoned")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="carts", verbose_name=_("user"))
    status = models.CharField(_("status"), max_length=255, choices=CartStatus.choices, default=CartStatus.OPEN)

    class Meta:
        verbose_name = _("cart")
        verbose_name_plural = _("carts")
        constraints = [
            # At most one open cart per user per club (CartStatus.OPEN == "open").
            UniqueConstraint(fields=["club", "user"], condition=Q(status="open"), name="unique_open_cart_per_user_per_club"),
        ]

    def __str__(self):
        return f"{self.user} - {self.status}"


class CartItem(UUIDModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items", verbose_name=_("cart"))
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="cart_items", verbose_name=_("product"))
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="cart_items", verbose_name=_("variant"), blank=True, null=True)

    quantity = models.PositiveSmallIntegerField(_("quantity"), default=1)
    unit_price = models.DecimalField(_("unit price"), max_digits=10, decimal_places=2)

    beneficiary = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="cart_items", verbose_name=_("beneficiary"), blank=True, null=True)
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="cart_items", verbose_name=_("team"), blank=True, null=True)
    #: Free-text per-item customization -- what to print on a jersey. Only
    #: collected when Product.personalization_enabled is on for this item's
    #: product (both blank otherwise); two fields since a jersey needs both a
    #: number and a name, independently optional (a club might print one
    #: without the other).
    personalization_number = models.CharField(_("number"), max_length=20, blank=True, help_text=_("Optional -- e.g. the player number to print on this item."))
    personalization_name = models.CharField(_("name"), max_length=100, blank=True, help_text=_("Optional -- e.g. the name to print on this item."))

    class Meta:
        verbose_name = _("cart item")
        verbose_name_plural = _("cart items")
        constraints = [
            UniqueConstraint(fields=["cart", "product", "variant", "beneficiary", "personalization_number", "personalization_name"], name="unique_product_per_cart_per_beneficiary"),
        ]

    def __str__(self):
        suffix = f" ({self.variant.name})" if self.variant_id else ""
        return f"{self.product}{suffix} - {self.quantity}x"

    def clean(self):
        club_id = self.cart.club_id if self.cart_id else None
        validate_club_scope(self, club_id, same_club_fields=("product", "team"), member_fields=("beneficiary",))
        if self.variant_id and self.product_id and self.variant.product_id != self.product_id:
            raise ValidationError({"variant": _("Must be a variant of this product.")})


class Order(ClubScopedModel):
    #: Independent from FulfillmentStatus on purpose -- "paid" and "picked up"
    #: are two different questions (see FulfillmentStatus's own docstring for
    #: why a single combined status field couldn't answer both at once
    #: without a member-comment.md-worthy JOIN against Payment every time
    #: something needed to know just one of them).
    class PaymentStatus(models.TextChoices):
        PENDING = "pending", _("Pending")
        PARTIALLY_PAID = "partially_paid", _("Partially paid")
        PAID = "paid", _("Paid")
        REFUNDED = "refunded", _("Refunded")

    class FulfillmentStatus(models.TextChoices):
        NOT_READY = "not_ready", _("Not ready")
        READY_FOR_PICKUP = "ready_for_pickup", _("Ready for pickup")
        DELIVERED = "delivered", _("Delivered")
        #: A fulfillment outcome, not a payment one -- an order can be
        #: cancelled before or after being paid (payment_status keeps
        #: tracking independently; a cancelled-but-unrefunded order is a
        #: real, valid combination, not a contradiction).
        CANCELLED = "cancelled", _("Cancelled")

    number = models.CharField(_("number"), max_length=255, blank=True)
    purchaser = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="orders", verbose_name=_("purchaser"))
    payment_status = models.CharField(_("payment status"), max_length=255, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    fulfillment_status = models.CharField(_("fulfillment status"), max_length=255, choices=FulfillmentStatus.choices, default=FulfillmentStatus.NOT_READY)
    total = models.DecimalField(_("total"), max_digits=10, decimal_places=2)
    #: Set (optionally) the moment an order is marked ready for pickup --
    #: shown to the member on their own order and in the notification that
    #: told them so (shop.services.notifications.dispatch_order_ready_for_pickup_notification).
    pickup_instructions = models.TextField(_("pickup instructions"), blank=True, help_text=_("Optional -- shown to the member, e.g. \"ask for it at the clubhouse desk\"."))

    # `created`/`modified` come from TimeStampedModel — declaring them here as well
    # would clash with the abstract base.

    class Meta:
        verbose_name = _("order")
        verbose_name_plural = _("orders")
        ordering = ["-created"]
        constraints = [
            UniqueConstraint(fields=["club", "number"], name="unique_order_number_per_club"),
        ]

    def __str__(self):
        return self.number

    def clean(self):
        validate_club_scope(self, self.club_id, member_fields=("purchaser",))

    @property
    def is_closed(self) -> bool:
        """Delivered *and* paid -- genuinely finished, nothing left to
        collect or hand over. A delivered-but-unpaid order is deliberately
        still "open": someone still needs to collect payment for it. Drives
        OrderListView's own default "hide closed" filter and
        shop.services.stats.order_kpis' open_orders count -- both read this
        exact property so the KPI strip and the list underneath it never
        disagree."""
        return self.fulfillment_status == self.FulfillmentStatus.DELIVERED and self.payment_status == self.PaymentStatus.PAID

    def generate_number(self):
        """Next per-club order number for the current year: ``ORD-<year>-<seq>``."""
        return next_scoped_number(self, "ORD")

    def save(self, *args, **kwargs):
        return save_with_number(self, lambda: super(Order, self).save(*args, **kwargs))


class OrderLine(UUIDModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="order_items", verbose_name=_("order"))
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_items", verbose_name=_("product"))
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="order_items", verbose_name=_("variant"), blank=True, null=True)

    quantity = models.PositiveSmallIntegerField(_("quantity"), default=1)
    unit_price = models.DecimalField(_("unit price"), max_digits=10, decimal_places=2)

    beneficiary = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="order_items", verbose_name=_("beneficiary"), blank=True, null=True)
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="order_items", verbose_name=_("team"), blank=True, null=True)
    line_total = models.DecimalField(_("line total"), max_digits=10, decimal_places=2)
    #: Carried over from CartItem.personalization_number/_name at checkout --
    #: see CartItem's own docstring.
    personalization_number = models.CharField(_("number"), max_length=20, blank=True, help_text=_("Optional -- e.g. the player number to print on this item."))
    personalization_name = models.CharField(_("name"), max_length=100, blank=True, help_text=_("Optional -- e.g. the name to print on this item."))

    class Meta:
        verbose_name = _("order line")
        verbose_name_plural = _("order lines")

    def __str__(self):
        suffix = f" ({self.variant.name})" if self.variant_id else ""
        return f"{self.product}{suffix} - {self.quantity}x"

    def clean(self):
        club_id = self.order.club_id if self.order_id else None
        validate_club_scope(self, club_id, same_club_fields=("product", "team"), member_fields=("beneficiary",))
        if self.variant_id and self.product_id and self.variant.product_id != self.product_id:
            raise ValidationError({"variant": _("Must be a variant of this product.")})


class Discount(ClubScopedModel):
    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=255, blank=True)
    description = models.TextField(_("description"), blank=True)

    #: What a member types in at checkout -- always compared uppercased (see
    #: save()) so "summer10"/"SUMMER10" match the same discount regardless of
    #: how either side happened to type it.
    code = models.CharField(_("code"), max_length=50, help_text=_("What members type in at checkout, e.g. SUMMER10. Not case-sensitive."))

    discount_type = models.CharField(_("discount type"), max_length=255, choices=DiscountType.choices, default=DiscountType.PERCENTAGE)
    discount_amount = models.DecimalField(_("discount amount"), max_digits=10, decimal_places=2, default=0)

    is_active = models.BooleanField(_("is active?"), default=True)

    slug_source = "name"

    class Meta:
        verbose_name = _("order discount type")
        verbose_name_plural = _("order discount types")
        ordering = ["name"]
        constraints = [
            UniqueConstraint(fields=["club", "slug"], name="unique_order_discount_type_slug_per_club"),
            UniqueConstraint(fields=["club", "code"], name="unique_discount_code_per_club"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.code = self.code.upper().strip()
        super().save(*args, **kwargs)


class AppliedDiscount(UUIDModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="applied_discounts", verbose_name=_("order"))
    discount = models.ForeignKey(Discount, on_delete=models.PROTECT, related_name="applied_discounts", verbose_name=_("discount"))

    discount_type = models.CharField(_("discount type"), max_length=255, choices=DiscountType.choices, default=DiscountType.PERCENTAGE)
    discount_amount = models.DecimalField(_("discount amount"), max_digits=10, decimal_places=2)
    description = models.TextField(_("description"), blank=True)

    applied_by = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="applied_discounts", verbose_name=_("applied by"), blank=True, null=True)

    class Meta:
        verbose_name = _("applied discount")
        verbose_name_plural = _("applied discounts")
        constraints = [
            UniqueConstraint(fields=["order", "discount"], name="unique_discount_per_order"),
        ]

    def __str__(self):
        suffix = "%" if self.discount_type == DiscountType.PERCENTAGE else ""
        return f"{self.discount} - {self.discount_amount}{suffix}"

    def clean(self):
        club_id = self.order.club_id if self.order_id else None
        validate_club_scope(self, club_id, same_club_fields=("discount",), member_fields=("applied_by",))


class Voucher(ClubScopedModel):
    """A prepaid credit for a fixed amount, redeemable in part or in full
    against one or more orders' Payments (Payment.voucher) -- consumed_amount
    tracks how much of it has been used so far, never reset or recomputed
    from Payments directly (shop.services.payments.record_payment/
    PaymentDeleteView are the only writers, keeping the two in lock-step
    instead of ever re-deriving one from the other)."""

    number = models.CharField(_("number"), max_length=255, blank=True)
    #: Optional -- who this credit was issued to, for the management list to
    #: search/filter by. Doesn't restrict which order it can be redeemed
    #: against (any order in the club); it's a record of provenance, not an
    #: ownership lock.
    issued_to = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="vouchers", verbose_name=_("issued to"), blank=True, null=True)
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2)
    consumed_amount = models.DecimalField(_("consumed amount"), max_digits=10, decimal_places=2, default=0)
    expiry_date = models.DateField(_("expiry date"))
    is_active = models.BooleanField(_("is active?"), default=True, help_text=_("Turn off to void a voucher before its expiry date, e.g. if it was issued by mistake."))
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("voucher")
        verbose_name_plural = _("vouchers")
        ordering = ["-created"]
        constraints = [
            UniqueConstraint(fields=["club", "number"], name="unique_voucher_number_per_club"),
        ]

    def __str__(self):
        return self.number

    def clean(self):
        validate_club_scope(self, self.club_id, member_fields=("issued_to",))
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
        """Next per-club voucher number for the current year: ``VCH-<year>-<seq>``."""
        return next_scoped_number(self, "VCH")

    def save(self, *args, **kwargs):
        return save_with_number(self, lambda: super(Voucher, self).save(*args, **kwargs))


class Payment(UUIDModel):
    class PaymentMethod(models.TextChoices):
        CREDIT_CARD = "credit_card", _("Credit card")
        BANK_TRANSFER = "bank_transfer", _("Bank transfer")
        CASH = "cash", _("Cash")
        VOUCHER = "voucher", _("Voucher")

    class PaymentStatus(models.TextChoices):
        PENDING = "pending", _("Pending")
        CONFIRMED = "confirmed", _("Confirmed")
        FAILED = "failed", _("Failed")
        REFUNDED = "refunded", _("Refunded")

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments", verbose_name=_("order"))
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2)
    method = models.CharField(_("method"), max_length=255, choices=PaymentMethod.choices, default=PaymentMethod.CREDIT_CARD)
    status = models.CharField(_("status"), max_length=255, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    reference = models.CharField(_("reference"), max_length=255, blank=True)
    paid_at = models.DateTimeField(_("paid at"), blank=True, null=True)
    #: Set only when method == VOUCHER (see clean()) -- which voucher this
    #: payment drew its amount from. shop.services.payments.record_payment is
    #: the only place that both creates a Payment and deducts from the
    #: voucher's own consumed_amount, in the same transaction.
    voucher = models.ForeignKey(Voucher, on_delete=models.PROTECT, related_name="payments", verbose_name=_("voucher"), blank=True, null=True)

    class Meta:
        verbose_name = _("payment")
        verbose_name_plural = _("payments")

    def __str__(self):
        return f"{self.order} - {self.status}"

    def clean(self):
        if self.method == self.PaymentMethod.VOUCHER and not self.voucher_id:
            raise ValidationError({"voucher": _("Required when the method is voucher.")})
        if self.method != self.PaymentMethod.VOUCHER and self.voucher_id:
            raise ValidationError({"voucher": _("Only allowed when the method is voucher.")})
        if self.voucher_id and self.order_id and self.voucher.club_id != self.order.club_id:
            raise ValidationError({"voucher": _("Must belong to the same club as the order.")})


class Invoice(ClubScopedModel):
    number = models.CharField(_("number"), max_length=255, blank=True)
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="invoice", verbose_name=_("order"))

    issued_at = models.DateTimeField(_("issued at"), auto_now_add=True)
    due_date = models.DateField(_("due date"), blank=True, null=True)

    billing_snapshot = models.JSONField(_("billing snapshot"), blank=True, null=True)  # Name/address information
    # No stored PDF field -- rendered on demand instead (shop.services.invoices.
    # render_invoice_pdf), same pattern as club/services/invoicing.py's dues
    # invoices. Simpler, and sidesteps ever needing to get storage/regeneration
    # right for a cached file.

    class Meta:
        verbose_name = _("invoice")
        verbose_name_plural = _("invoices")
        ordering = ["-issued_at"]
        constraints = [
            UniqueConstraint(fields=["club", "number"], name="unique_invoice_number_per_club"),
        ]

    def __str__(self):
        return self.number

    def clean(self):
        validate_club_scope(self, self.club_id, same_club_fields=("order",))

    def generate_number(self):
        """Next per-club invoice number for the current year: ``INV-<year>-<seq>``."""
        return next_scoped_number(self, "INV")

    def save(self, *args, **kwargs):
        return save_with_number(self, lambda: super(Invoice, self).save(*args, **kwargs))
