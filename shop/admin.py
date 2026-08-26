from django.contrib import admin

from .models import (
    AppliedDiscount,
    Cart,
    CartItem,
    Discount,
    Invoice,
    Order,
    OrderLine,
    Payment,
    Product,
    ProductCategory,
    ProductVariant,
)


class ClubScopedFKMixin:
    """Restrict club-scoped FK dropdowns to the edited object's (or parent's) club.

    ``scoped_fk_fields`` names FKs whose target is a club-scoped model. The club
    is taken from the object being edited (or, for inlines, the parent object).
    Enforcement still lives in each model's ``clean()``; this just keeps invalid
    options out of the dropdowns.
    """

    scoped_fk_fields = ()

    def get_form(self, request, obj=None, **kwargs):
        request._club_obj = obj
        return super().get_form(request, obj, **kwargs)

    def get_formset(self, request, obj=None, **kwargs):
        request._club_obj = obj
        return super().get_formset(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name in self.scoped_fk_fields:
            club_id = getattr(getattr(request, "_club_obj", None), "club_id", None)
            if club_id is not None:
                kwargs["queryset"] = db_field.related_model._default_manager.filter(club_id=club_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0


@admin.register(Product)
class ProductAdmin(ClubScopedFKMixin, admin.ModelAdmin):
    scoped_fk_fields = ("season", "staff_role", "category")
    list_display = ["name", "product_type", "category", "club", "is_active", "is_public"]
    list_filter = ["club", "product_type", "is_active", "is_public"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [ProductVariantInline]


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "club"]
    list_filter = ["club"]
    search_fields = ["name"]


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ["name", "product", "price", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "product__name"]
    raw_id_fields = ["product"]


class CartItemInline(ClubScopedFKMixin, admin.TabularInline):
    model = CartItem
    # variant excluded from scoped_fk_fields -- ProductVariant has no club FK of
    # its own for the mixin's flat club_id filter to use (see its own docstring);
    # raw_id_fields below is enough for this admin-only, low-traffic dropdown.
    scoped_fk_fields = ("product", "team")
    extra = 0
    raw_id_fields = ["beneficiary", "variant"]


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ["user", "status", "club"]
    list_filter = ["club", "status"]
    search_fields = ["user__email"]
    raw_id_fields = ["user"]
    inlines = [CartItemInline]


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ["cart", "product", "variant", "quantity", "beneficiary"]
    search_fields = ["product__name"]
    raw_id_fields = ["cart", "product", "variant", "beneficiary", "team"]


class OrderLineInline(ClubScopedFKMixin, admin.TabularInline):
    model = OrderLine
    # Same variant exclusion as CartItemInline above -- see that one's own comment.
    scoped_fk_fields = ("product", "team")
    extra = 0
    raw_id_fields = ["beneficiary", "variant"]


class AppliedDiscountInline(ClubScopedFKMixin, admin.TabularInline):
    model = AppliedDiscount
    scoped_fk_fields = ("discount",)
    extra = 0
    raw_id_fields = ["applied_by"]


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["number", "purchaser", "payment_status", "fulfillment_status", "total", "club", "created"]
    list_filter = ["club", "payment_status", "fulfillment_status"]
    search_fields = ["number", "purchaser__first_name", "purchaser__last_name"]
    raw_id_fields = ["purchaser"]
    readonly_fields = ["number", "created", "modified"]
    inlines = [OrderLineInline, AppliedDiscountInline, PaymentInline]


@admin.register(OrderLine)
class OrderLineAdmin(admin.ModelAdmin):
    list_display = ["order", "product", "variant", "quantity", "line_total"]
    search_fields = ["product__name", "order__number"]
    raw_id_fields = ["order", "product", "variant", "beneficiary", "team"]


@admin.register(Discount)
class DiscountAdmin(admin.ModelAdmin):
    list_display = ["name", "discount_type", "discount_amount", "club", "is_active"]
    list_filter = ["club", "discount_type", "is_active"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}


@admin.register(AppliedDiscount)
class AppliedDiscountAdmin(admin.ModelAdmin):
    list_display = ["order", "discount", "discount_type", "discount_amount"]
    search_fields = ["order__number", "discount__name"]
    raw_id_fields = ["order", "discount", "applied_by"]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["order", "amount", "method", "status", "paid_at"]
    list_filter = ["method", "status"]
    search_fields = ["order__number", "reference"]
    raw_id_fields = ["order"]


@admin.register(Invoice)
class InvoiceAdmin(ClubScopedFKMixin, admin.ModelAdmin):
    scoped_fk_fields = ("order",)
    list_display = ["number", "order", "club", "issued_at", "due_date"]
    list_filter = ["club"]
    search_fields = ["number", "order__number"]
    readonly_fields = ["number", "issued_at"]
