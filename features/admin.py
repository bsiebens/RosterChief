from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet
from django.utils.html import format_html_join

from rosterchief.admin import PlatformAdminMixin

from .catalog import duplicate_limit_scopes
from .models import EntitlementLog, Feature, FeatureOverride, Plan, PlanFeature, Subscription
from .services import clear_override, end_subscription, log, set_override, subscribe


class ReadOnlyMixin:
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Feature)
class FeatureAdmin(PlatformAdminMixin, ReadOnlyMixin, admin.ModelAdmin):
    """Defined in code (features/catalog.py); synced by `manage.py sync_features`."""

    list_display = ["code", "name", "kind", "is_active"]
    search_fields = ["code", "name"]


class PlanFeatureFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        codes = [f.cleaned_data["feature"].code for f in self.forms if f.cleaned_data.get("feature") and not f.cleaned_data.get("DELETE")]
        if duplicate_limit_scopes(codes):
            raise ValidationError("A plan can only have one feature limiting the same thing (e.g. the number of members).")


class PlanFeatureInline(admin.TabularInline):
    model = PlanFeature
    formset = PlanFeatureFormSet
    extra = 0


@admin.register(Plan)
class PlanAdmin(PlatformAdminMixin, admin.ModelAdmin):
    list_display = ["name", "code", "is_active"]
    inlines = [PlanFeatureInline]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        plan = form.instance
        features = {pf.feature.code: pf.limit for pf in plan.plan_features.select_related("feature")}
        log("plan_updated", plan=plan, new={"is_active": plan.is_active, "features": features}, actor=request.user)

    def has_delete_permission(self, request, obj=None):
        return False  # deactivate instead; subscriptions reference plans


@admin.register(Subscription)
class SubscriptionAdmin(PlatformAdminMixin, admin.ModelAdmin):
    """Adding a subscription moves the club to that plan (the previous one ends now). History is read-only."""

    list_display = ["club", "plan", "status", "starts_at", "ends_at"]
    list_filter = ["plan", "status"]
    autocomplete_fields = []
    actions = ["end_selected"]

    def get_fields(self, request, obj=None):
        return ["club", "plan", "status", "trial_ends_at", "ends_at"] if obj is None else ["club", "plan", "status", "starts_at", "trial_ends_at", "ends_at"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        sub = subscribe(obj.club, obj.plan, status=obj.status, trial_ends_at=obj.trial_ends_at, ends_at=obj.ends_at, actor=request.user)
        obj.pk, obj._state.adding = sub.pk, False

    @admin.action(description="End selected open subscriptions now")
    def end_selected(self, request, queryset):
        for sub in queryset.filter(ends_at__isnull=True).select_related("club"):
            end_subscription(sub.club, actor=request.user, reason="Ended in admin")
        self.message_user(request, "Subscriptions ended.")


@admin.register(FeatureOverride)
class FeatureOverrideAdmin(PlatformAdminMixin, admin.ModelAdmin):
    list_display = ["club", "feature", "enabled", "limit", "expires_at", "reason"]
    list_filter = ["feature", "enabled"]

    def save_model(self, request, obj, form, change):
        ov = set_override(obj.club, obj.feature.code, enabled=obj.enabled, limit=obj.limit, expires_at=obj.expires_at, reason=obj.reason, actor=request.user)
        obj.pk, obj._state.adding = ov.pk, False

    def delete_model(self, request, obj):
        clear_override(obj.club, obj.feature.code, actor=request.user, reason="Removed in admin")

    def delete_queryset(self, request, queryset):
        for ov in queryset.select_related("club", "feature"):
            clear_override(ov.club, ov.feature.code, actor=request.user, reason="Removed in admin")


@admin.register(EntitlementLog)
class EntitlementLogAdmin(PlatformAdminMixin, ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["created", "action", "club", "plan", "feature", "actor", "reason"]
    list_filter = ["action"]
    search_fields = ["club__name", "reason"]
    date_hierarchy = "created"


def effective_features_html(club):
    """Table of a club's effective entitlements, for the club admin page."""
    from .catalog import CATALOG
    from .entitlements import resolve_entitlements

    ent = resolve_entitlements(club)
    rows = []
    for code, f in CATALOG.items():
        if not ent.has(code):
            rows.append((f.name, "off"))
        elif f.kind == "limit":
            rows.append((f.name, "unlimited" if ent.limit(code) is None else f"limit {ent.limit(code)}"))
        else:
            rows.append((f.name, "on"))
    return format_html_join("", "<div>{}: <strong>{}</strong></div>", rows)
