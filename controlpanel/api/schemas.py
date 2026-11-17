import datetime as _dt
from datetime import datetime
from uuid import UUID

from ninja import Schema
from pydantic import Field


class MeOut(Schema):
    id: UUID
    email: str
    first_name: str
    last_name: str
    level: str  # "staff" or "superuser"
    version: str  # the platform version (pyproject.toml)


class PlatformAdminOut(Schema):
    id: UUID
    email: str
    first_name: str
    last_name: str
    is_staff: bool
    is_superuser: bool
    mfa_enabled: bool

    @staticmethod
    def from_perms(perms) -> PlatformAdminOut:
        from allauth.mfa.utils import is_mfa_enabled

        user = perms.profile
        return PlatformAdminOut(
            id=user.pk,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            is_staff=perms.is_staff or perms.is_superuser,
            is_superuser=perms.is_superuser,
            mfa_enabled=is_mfa_enabled(user),
        )


class PlatformAdminGrantIn(Schema):
    email: str
    first_name: str = ""
    last_name: str = ""
    is_superuser: bool = False


class PlatformAdminUpdateIn(Schema):
    is_staff: bool = True
    is_superuser: bool = False


class AuditLogOut(Schema):
    id: UUID
    created: datetime
    action: str
    actor_id: UUID | None = None
    actor_email: str | None = None
    target_type: str
    target_id: str
    club_id: UUID | None = None
    data: dict

    @staticmethod
    def resolve_actor_email(obj):
        return obj.actor.email if obj.actor_id else None


# --- clubs ---------------------------------------------------------------------------------------------------------


class SportRef(Schema):
    id: UUID
    name: str
    slug: str


class ClubListOut(Schema):
    id: UUID
    name: str
    slug: str
    schema_name: str
    sport: SportRef | None = None
    owner_email: str
    plan: str | None = None  # code of the current subscription's plan; None = default plan applies
    archived_at: datetime | None = None
    created: datetime
    logo_url: str | None = None  # site-relative (MEDIA_URL), None when the club has no logo

    @staticmethod
    def of(club) -> ClubListOut:
        return ClubListOut(
            id=club.pk,
            name=club.name,
            slug=club.slug,
            schema_name=club.schema_name,
            sport=SportRef(id=club.sport.pk, name=club.sport.name, slug=club.sport.slug) if club.sport else None,
            owner_email=club.owner.email,
            plan=getattr(club, "plan_code", None),
            archived_at=club.archived_at,
            created=club.created,
            logo_url=club.logo.url if club.logo else None,
        )


class ClubAdminOut(Schema):
    id: UUID
    email: str
    first_name: str
    last_name: str
    is_owner: bool  # the club's registered owner (cannot be removed as administrator)

    @staticmethod
    def of(user, club) -> ClubAdminOut:
        return ClubAdminOut(id=user.pk, email=user.email, first_name=user.first_name, last_name=user.last_name, is_owner=user.pk == club.owner_id)


class ClubDetailOut(ClubListOut):
    legal_name: str
    contact_email: str
    website: str
    legal_address: str
    legal_zip_code: str
    legal_city: str
    legal_country: str  # ISO 3166-1 alpha-2 code, "" when not set
    legal_country_name: str
    vat_id: str
    season_start: str  # ISO date; only its month and day matter
    season_duration_months: int
    primary_color: str  # "#rrggbb" or "" when the club uses the theme default
    secondary_color: str
    domains: list[str]
    admins: list[ClubAdminOut]

    @staticmethod
    def of(club, domains, admins) -> ClubDetailOut:
        base = ClubListOut.of(club).dict()
        return ClubDetailOut(
            **base,
            legal_name=club.legal_name,
            contact_email=club.contact_email,
            website=club.website,
            legal_address=club.legal_address,
            legal_zip_code=club.legal_zip_code,
            legal_city=club.legal_city,
            legal_country=club.legal_country.code or "",
            legal_country_name=club.legal_country.name if club.legal_country else "",
            vat_id=club.vat_id,
            season_start=club.season_start.isoformat(),
            season_duration_months=club.season_duration_months,
            primary_color=club.primary_color,
            secondary_color=club.secondary_color,
            domains=domains,
            admins=[ClubAdminOut.of(u, club) for u in admins],
        )


class ClubCreateIn(Schema):
    name: str
    sport_id: UUID | None = None
    owner_email: str
    owner_first_name: str = ""  # required only if the owner has no account yet
    owner_last_name: str = ""
    legal_name: str | None = None
    contact_email: str | None = None
    website: str | None = None
    legal_address: str | None = None
    legal_zip_code: str | None = None
    legal_city: str | None = None
    legal_country: str | None = None  # ISO 3166-1 alpha-2 code, e.g. "BE"
    vat_id: str | None = None


class ClubUpdateIn(Schema):
    name: str | None = None
    sport_id: UUID | None = None
    legal_name: str | None = None
    contact_email: str | None = None
    website: str | None = None
    legal_address: str | None = None
    legal_zip_code: str | None = None
    legal_city: str | None = None
    legal_country: str | None = None
    vat_id: str | None = None
    season_start: _dt.date | None = None
    season_duration_months: int | None = None
    primary_color: str | None = None  # "#rrggbb"; "" resets to the theme default
    secondary_color: str | None = None


class ClubDeleteIn(Schema):
    confirm_slug: str


class ClubAdminIn(Schema):
    email: str
    first_name: str = ""
    last_name: str = ""


# --- plans, subscriptions, overrides --------------------------------------------------------------------------------


class FeatureOut(Schema):
    code: str
    name: str
    description: str
    kind: str  # "flag" or "limit"
    limit_scope: str | None = None
    is_active: bool


class PlanFeatureIO(Schema):
    code: str
    limit: int | None = None  # only for limit features; empty = unlimited


class PlanFeatureOut(Schema):
    code: str
    name: str
    kind: str
    limit: int | None = None


class PlanOrderIn(Schema):
    plan_ids: list[UUID]


class PlanOut(Schema):
    id: UUID
    code: str
    name: str
    description: str
    is_active: bool
    position: int = 0
    is_default: bool
    subscriber_count: int
    features: list[PlanFeatureOut]

    @staticmethod
    def of(plan) -> PlanOut:
        from features.catalog import DEFAULT_PLAN
        from features.models import Subscription

        return PlanOut(
            id=plan.pk,
            code=plan.code,
            name=plan.name,
            description=plan.description,
            is_active=plan.is_active,
            position=plan.position,
            is_default=plan.code == DEFAULT_PLAN,
            subscriber_count=Subscription.objects.filter(plan=plan, ends_at__isnull=True).count(),
            features=[PlanFeatureOut(code=pf.feature.code, name=pf.feature.name, kind=pf.feature.kind, limit=pf.limit) for pf in plan.plan_features.select_related("feature").order_by("feature__code")],
        )


class PlanCreateIn(Schema):
    code: str
    name: str
    description: str = ""
    features: list[PlanFeatureIO] = []


class PlanUpdateIn(Schema):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None
    features: list[PlanFeatureIO] | None = None  # when given, replaces the plan's features


class PlanRef(Schema):
    id: UUID
    code: str
    name: str


class SubscriptionOut(Schema):
    id: UUID
    plan: PlanRef
    status: str
    starts_at: datetime
    ends_at: datetime | None = None
    trial_ends_at: datetime | None = None
    is_current: bool

    @staticmethod
    def of(sub, now=None) -> SubscriptionOut:
        return SubscriptionOut(
            id=sub.pk,
            plan=PlanRef(id=sub.plan.pk, code=sub.plan.code, name=sub.plan.name),
            status=sub.status,
            starts_at=sub.starts_at,
            ends_at=sub.ends_at,
            trial_ends_at=sub.trial_ends_at,
            is_current=sub.grants_access(now),
        )


class SubscribeIn(Schema):
    plan_id: UUID
    status: str = "active"
    trial_ends_at: datetime | None = None
    ends_at: datetime | None = None
    reason: str = ""


class EndSubscriptionIn(Schema):
    reason: str = ""


class EntitlementOut(Schema):
    code: str
    name: str
    kind: str
    enabled: bool
    limit: int | None = None
    source: str  # plan | default_plan | overdue (downgraded for unpaid billing) | override | none


class OverrideIn(Schema):
    enabled: bool = True
    limit: int | None = None
    expires_at: datetime | None = None
    reason: str = ""


class OverrideOut(Schema):
    feature_code: str
    feature_name: str
    enabled: bool
    limit: int | None = None
    expires_at: datetime | None = None
    reason: str
    is_current: bool

    @staticmethod
    def of(ov) -> OverrideOut:
        return OverrideOut(feature_code=ov.feature.code, feature_name=ov.feature.name, enabled=ov.enabled, limit=ov.limit, expires_at=ov.expires_at, reason=ov.reason, is_current=ov.is_current())


class EntitlementLogOut(Schema):
    id: UUID
    created: datetime
    action: str
    club_id: UUID | None = None
    plan_code: str | None = None
    feature_code: str | None = None
    actor_email: str | None = None
    old: dict | None = None
    new: dict | None = None
    reason: str

    @staticmethod
    def resolve_plan_code(obj):
        return obj.plan.code if obj.plan_id else None

    @staticmethod
    def resolve_feature_code(obj):
        return obj.feature.code if obj.feature_id else None

    @staticmethod
    def resolve_actor_email(obj):
        return obj.actor.email if obj.actor_id else None


# --- reference data -------------------------------------------------------------------------------------------------


class SportOut(Schema):
    id: UUID
    name: str
    slug: str
    club_count: int
    competition_count: int
    referee_type_count: int
    lineup_slots_enabled: bool = False
    lineup_template: list[dict] = []


class SportIn(Schema):
    name: str


class SportLineupSlotsIn(Schema):
    """Informational only: slots are never used for statistics."""

    enabled: bool
    template: list[dict] = []


class RefereeTypeOut(Schema):
    id: UUID
    sport_id: UUID
    kind: str
    name: str
    ordering: int


class RefereeTypeCreateIn(Schema):
    kind: str = "referee"
    name: str


class RefereeTypeUpdateIn(Schema):
    name: str


class RefereeTypeReorderIn(Schema):
    kind: str
    ids: list[UUID]


class CompetitionOut(Schema):
    id: UUID
    name: str
    module: str
    sport_id: UUID | None = None
    sport_name: str | None = None
    feature_code: str | None = None

    @staticmethod
    def of(c) -> CompetitionOut:
        return CompetitionOut(id=c.pk, name=c.name, module=c.module, sport_id=c.sport_id, sport_name=c.sport.name if c.sport_id else None, feature_code=c.feature.code if c.feature_id else None)


class CompetitionIn(Schema):
    name: str
    module: str
    sport_id: UUID | None = None
    feature_code: str | None = None


class CompetitionUpdateIn(Schema):
    name: str | None = None
    module: str | None = None
    sport_id: UUID | None = None
    feature_code: str | None = None


# --- platform switches ----------------------------------------------------------------------------------------------


class SwitchState(Schema):
    is_active: bool
    started_at: datetime | None = None
    started_by_email: str | None = None


class MaintenanceState(SwitchState):
    message: str


class PushSandboxState(Schema):
    is_active: bool
    expires_at: datetime | None = None


class ManualInvoicingState(Schema):
    """``is_active`` is true while automatic club invoicing is OFF (the banner shows then); ``pending_count`` = clubs that need an invoice now."""

    is_active: bool
    pending_count: int = 0


class SwitchesOut(Schema):
    maintenance: MaintenanceState
    email_suppression: SwitchState
    push_sandbox: PushSandboxState
    manual_invoicing: ManualInvoicingState


class MaintenanceIn(Schema):
    active: bool
    message: str = ""


class EmailSuppressionIn(Schema):
    active: bool


class ProvisioningOut(Schema):
    id: UUID
    name: str
    status: str  # pending | running | succeeded | failed
    club_id: UUID | None = None
    club_slug: str | None = None
    error: str = ""
    created: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @staticmethod
    def of(job) -> ProvisioningOut:
        return ProvisioningOut(
            id=job.pk,
            name=job.name,
            status=job.status,
            club_id=job.club_id,
            club_slug=job.club.slug if job.club_id else None,
            error=job.error,
            created=job.created,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )


# --- billing ---------------------------------------------------------------------------------------------------------

from datetime import date  # noqa: E402
from decimal import Decimal  # noqa: E402


class CycleRef(Schema):
    """A billing cycle in short: what it is called and how long a period lasts."""

    id: UUID
    name: str
    duration_months: int

    @staticmethod
    def of(cycle) -> CycleRef | None:
        return CycleRef(id=cycle.pk, name=cycle.display_name, duration_months=cycle.duration_months) if cycle is not None else None


class PriceIn(Schema):
    active_from: date
    amount: Decimal


class PriceOut(Schema):
    id: UUID
    active_from: date
    amount: Decimal


class CycleIn(Schema):
    label: str = ""  # optional custom name; empty = named after the length (Monthly, Quarterly, Yearly ...)
    duration_months: int = 12
    renewal_lead_days: int = 30
    grace_days: int = 30
    is_active: bool = True


class CyclePatch(Schema):
    label: str | None = None
    duration_months: int | None = None
    renewal_lead_days: int | None = None
    grace_days: int | None = None
    is_active: bool | None = None


class CycleOrderIn(Schema):
    cycle_ids: list[UUID]


class PlanCycleOut(Schema):
    id: UUID
    plan_id: UUID
    name: str  # display name: the label, else Monthly / Quarterly / Yearly / Every N months
    label: str
    duration_months: int
    renewal_lead_days: int
    grace_days: int
    position: int
    is_active: bool
    prices: list[PriceOut]
    current_price: Decimal | None = None  # None = clubs cannot be billed on this cycle (no price yet)
    club_count: int  # billed clubs on it now

    @staticmethod
    def of(cycle) -> PlanCycleOut:
        from billing.models import ClubBilling
        from billing.services.dues import price_on, today

        price = price_on(cycle.plan, today(), cycle)
        return PlanCycleOut(
            id=cycle.pk,
            plan_id=cycle.plan_id,
            name=cycle.display_name,
            label=cycle.label,
            duration_months=cycle.duration_months,
            renewal_lead_days=cycle.renewal_lead_days,
            grace_days=cycle.grace_days,
            position=cycle.position,
            is_active=cycle.is_active,
            prices=[PriceOut(id=p.pk, active_from=p.active_from, amount=p.amount) for p in cycle.prices.all()],
            current_price=price.amount if price else None,
            club_count=ClubBilling.objects.filter(cycle=cycle, club__subscriptions__plan=cycle.plan, club__subscriptions__ends_at__isnull=True).distinct().count(),
        )


class BillingPlanOut(Schema):
    plan: PlanRef
    is_active: bool
    position: int = 0
    cycles: list[PlanCycleOut]  # in the order the platform chose; the first active one is the default
    club_count: int

    @staticmethod
    def of(plan) -> BillingPlanOut:
        from features.models import Subscription

        return BillingPlanOut(
            plan=PlanRef(id=plan.pk, code=plan.code, name=plan.name),
            is_active=plan.is_active,
            position=plan.position,
            cycles=[PlanCycleOut.of(c) for c in plan.cycles.order_by("position", "duration_months")],
            club_count=Subscription.objects.filter(plan=plan, ends_at__isnull=True, club__billing__isnull=False).count(),
        )


class PaymentIn(Schema):
    amount: Decimal
    method: str = "bank_transfer"
    reference: str = ""
    paid_at: datetime | None = None
    note: str = ""


class PaymentOut(Schema):
    id: UUID
    amount: Decimal
    method: str
    reference: str
    paid_at: datetime
    note: str
    recorded_by_email: str | None = None

    @staticmethod
    def of(p) -> PaymentOut:
        return PaymentOut(id=p.pk, amount=p.amount, method=p.method, reference=p.reference, paid_at=p.paid_at, note=p.note, recorded_by_email=p.recorded_by.email if p.recorded_by_id else None)


class InvoiceOut(Schema):
    number: str
    issued_at: date
    sent_at: datetime | None = None
    sent_method: str = ""
    custom_number: bool  # True when the number was typed in at issue time instead of coming from the automatic sequence
    needs_sending: bool
    net: Decimal
    vat: Decimal
    total: Decimal
    vat_rate: Decimal
    reverse_charge: bool
    due_date: date
    has_snapshot: bool  # False for invoices issued before snapshots existed (the PDF then renders from live data)
    status: str = "active"  # active | voided (a voided invoice keeps its number, cannot be sent, still has a PDF)
    voided_at: datetime | None = None
    void_reason: str = ""
    replaced_by_number: str | None = None  # on a voided invoice: the invoice that replaced it
    replaces_number: str | None = None  # on a replacement: the voided invoice it replaces

    @staticmethod
    def of(inv, *, siblings=None) -> InvoiceOut:
        """``siblings``: all invoices of the period (when already loaded), which saves a query per link."""
        snap = inv.snapshot or {}
        due = inv.due
        if siblings is not None:
            by_id = {i.id: i for i in siblings}
            replaced_by = by_id.get(inv.replaced_by_id)
            replaces = next((i for i in siblings if i.replaced_by_id == inv.id), None)
        else:
            replaced_by = inv.replaced_by if inv.replaced_by_id else None
            replaces = inv.replaces
        return InvoiceOut(
            status=inv.status,
            voided_at=inv.voided_at,
            void_reason=inv.void_reason,
            replaced_by_number=replaced_by.number if replaced_by else None,
            replaces_number=replaces.number if replaces else None,
            number=inv.number,
            issued_at=inv.issued_at,
            sent_at=inv.sent_at,
            sent_method=inv.sent_method,
            custom_number=inv.custom_number,
            needs_sending=inv.needs_sending,
            net=Decimal(snap.get("net", due.amount)),
            vat=Decimal(snap.get("vat", due.vat_amount)),
            total=Decimal(snap.get("total", due.total)),
            vat_rate=Decimal(snap.get("vat_rate", due.vat_rate)),
            reverse_charge=snap.get("reverse_charge", due.reverse_charge),
            due_date=date.fromisoformat(snap["due_date"]) if snap.get("due_date") else due.grace_until,
            has_snapshot=bool(snap),
        )


class NumberingPreviewIn(Schema):
    invoice_number_pattern: str | None = None  # default: the saved pattern
    credit_note_number_pattern: str | None = None
    date: _dt.date | None = None  # default today; the date the example and the counter scope are computed for


class NumberingKindOut(Schema):
    valid: bool
    error: str | None = None
    example: str | None = None  # the pattern rendered with sequence 1 for the date
    next: str | None = None  # the real next automatic number under this pattern with the current counter of its scope
    scope: str | None = None  # counter key: "2026", "2026-11" or "all"
    scope_label: str | None = None  # "2026", "November 2026" or "All time"
    resets: str | None = None  # yearly | monthly | never


class PlaceholderOut(Schema):
    token: str
    meaning: str


class NumberingPreviewOut(Schema):
    invoice: NumberingKindOut
    credit_note: NumberingKindOut
    placeholders: list[PlaceholderOut]
    literal_characters: str
    max_length: int


class NumberEchoOut(Schema):
    mode: str  # "custom" | "automatic"
    number: str | None = None  # the custom number (null when automatic)
    next_automatic: str  # what the automatic counter would give right now


class InvoiceIssueIn(Schema):
    number: str | None = None  # custom number; omit for the automatic INV-YYYY-00001 sequence


class InvoiceVoidIn(Schema):
    reason: str = Field(min_length=1, max_length=300)  # required; printed on the voided invoice's record and the credit note
    number: str | None = None  # custom number for the replacement; omit for the next automatic one


class IssuerSettingsIO(Schema):
    """The platform's invoicing identity. ``country`` is an ISO 3166-1 alpha-2 code or empty."""

    legal_name: str = ""
    address: str = ""
    zip_code: str = ""
    city: str = ""
    country: str = ""
    vat_id: str = ""
    iban: str = ""
    bic: str = ""
    contact_email: str = ""
    website: str = ""
    invoice_footer: str = ""
    payment_terms: str = ""
    vat_rate: Decimal = Decimal("21.00")  # percent, 0 = no VAT
    archive_after_days: int = 30  # unpaid clubs are downgraded first, then archived this many days later (0-365)
    # Automatic invoicing (BillingSettings.auto_invoicing). GET always returns it; on PUT, omitted/null = leave unchanged (so an older client cannot switch it back on by accident).
    auto_invoicing: bool | None = None
    # Automatic numbering (see billing.services.numbering). Optional on PUT: omitted = the current/default value.
    invoice_number_pattern: str = "INV-{YYYY}-{NNNNN}"
    credit_note_number_pattern: str = "CN-{YYYY}-{NNNNN}"
    # PUT only: jump the counter forward so the NEXT number uses this sequence value (> the current counter; null = leave alone). GET returns null.
    invoice_next_sequence: int | None = Field(None, ge=1)
    credit_note_next_sequence: int | None = Field(None, ge=1)
    # GET/PUT responses only (ignored on PUT): what the next automatic number would be right now, and where the counter stands in today's scope.
    invoice_next_number_preview: str = ""
    credit_note_next_number_preview: str = ""
    invoice_current_sequence: int = 0  # last sequence value used in today's scope (0 = none yet)
    credit_note_current_sequence: int = 0
    invoice_counter_resets: str = "yearly"  # yearly | monthly | never
    credit_note_counter_resets: str = "yearly"

    @staticmethod
    def of(row) -> IssuerSettingsIO:
        from django.utils import timezone as django_timezone

        from billing.models import CreditNoteSequence, InvoiceSequence
        from billing.services import credit_notes as cn_svc
        from billing.services import invoices as inv_svc
        from billing.services import numbering

        today = django_timezone.localdate()
        return IssuerSettingsIO(
            invoice_number_pattern=row.invoice_number_pattern,
            credit_note_number_pattern=row.credit_note_number_pattern,
            invoice_next_number_preview=inv_svc.peek_next_automatic_number(row.invoice_number_pattern, today),
            credit_note_next_number_preview=cn_svc.peek_next_credit_note_number(row.credit_note_number_pattern, today),
            invoice_current_sequence=numbering.current_value(InvoiceSequence, numbering.scope_key(row.invoice_number_pattern, today)),
            credit_note_current_sequence=numbering.current_value(CreditNoteSequence, numbering.scope_key(row.credit_note_number_pattern, today)),
            invoice_counter_resets=numbering.resets(row.invoice_number_pattern),
            credit_note_counter_resets=numbering.resets(row.credit_note_number_pattern),
            legal_name=row.legal_name,
            address=row.address,
            zip_code=row.zip_code,
            city=row.city,
            country=str(row.country or ""),
            vat_id=row.vat_id,
            iban=row.iban,
            bic=row.bic,
            contact_email=row.contact_email,
            website=row.website,
            invoice_footer=row.invoice_footer,
            payment_terms=row.payment_terms,
            vat_rate=row.vat_rate,
            archive_after_days=row.archive_after_days,
            auto_invoicing=row.auto_invoicing,
        )


class ClubRef(Schema):
    id: UUID
    name: str
    slug: str


class CycleOut(Schema):
    club: ClubRef
    plan: PlanRef
    current_period_end: date | None = None
    next_period_start: date
    invoice_opens_on: date  # next_period_start minus the plan's renewal lead
    renewal_lead_days: int
    opened: bool  # the next period already exists
    invoice_due_now: bool  # invoice_opens_on has passed and the period is not opened yet
    trial_ends_at: date | None = None
    lapsed: bool = False  # the last period ended and nothing follows: opened regardless of auto_renew
    downgraded_at: datetime | None = None

    @staticmethod
    def of(row, day) -> CycleOut:
        club, plan = row["club"], row["plan"]
        return CycleOut(
            club=ClubRef(id=club.pk, name=club.name, slug=club.slug),
            plan=PlanRef(id=plan.pk, code=plan.code, name=plan.name),
            current_period_end=row["current_period_end"],
            next_period_start=row["next_period_start"],
            invoice_opens_on=row["invoice_opens_on"],
            renewal_lead_days=row["renewal_lead_days"],
            opened=row["opened"],
            invoice_due_now=(not row["opened"]) and row["invoice_opens_on"] <= day,
            trial_ends_at=row["trial_ends_at"],
            lapsed=row["lapsed"],
            downgraded_at=row["downgraded_at"],
        )


class CreditNoteBrief(Schema):
    number: str
    total: Decimal
    kind: str = "plan_change"  # plan_change | void


class CreditAppliedOut(Schema):
    due_id: UUID
    invoice_number: str | None = None
    amount: Decimal


class CreditNoteOut(Schema):
    id: UUID
    number: str  # CN-YYYY-00001, its own gapless sequence
    issued_at: date
    club: ClubRef
    invoice_number: str  # the invoice this credit note corrects (which is never edited)
    due_id: UUID
    net: Decimal  # amounts are positive: the credited part
    vat: Decimal
    total: Decimal
    vat_rate: Decimal
    reverse_charge: bool
    reason: str
    applied_to: list[CreditAppliedOut] = []  # informational: invoices that were paid from the credit this change created (always empty for void notes)
    kind: str = "plan_change"  # plan_change | void (a void note is a document only: no credit, no change to the period)
    replacement_invoice_number: str | None = None  # void notes: the invoice that replaced the voided one

    @staticmethod
    def of(note, *, with_applied: bool = False) -> CreditNoteOut:
        applied = []
        if with_applied and note.kind != "void":
            from billing.models import ClubCreditEntry

            for e in ClubCreditEntry.objects.filter(credit_note=note, kind=ClubCreditEntry.APPLIED).select_related("due"):
                inv = getattr(e.due, "invoice", None)
                applied.append(CreditAppliedOut(due_id=e.due_id, invoice_number=inv.number if inv else None, amount=-e.amount))
        return CreditNoteOut(
            id=note.pk,
            number=note.number,
            issued_at=note.issued_at,
            club=ClubRef(id=note.club.pk, name=note.club.name, slug=note.club.slug),
            invoice_number=note.invoice.number,
            due_id=note.due_id,
            net=note.net,
            vat=note.vat,
            total=note.total,
            vat_rate=note.vat_rate,
            reverse_charge=note.reverse_charge,
            reason=note.reason,
            applied_to=applied,
            kind=note.kind,
            replacement_invoice_number=note.replacement_invoice.number if note.replacement_invoice_id else None,
        )


class VoidReissueOut(Schema):
    voided: InvoiceOut  # the old invoice, now status "voided"
    invoice: InvoiceOut  # the replacement (same period, next number, needs sending)
    credit_note: CreditNoteOut | None = None  # null when the voided invoice had no amount


class CreditEntryOut(Schema):
    id: UUID
    amount: Decimal  # + credit gained, - credit used
    kind: str  # overpayment | cancelled_period | applied
    note: str
    created: datetime
    credit_note_number: str | None = None
    due_id: UUID | None = None


class CreditLedgerOut(Schema):
    balance: Decimal
    entries: list[CreditEntryOut]  # newest first


class VoidedInvoiceOut(Schema):
    number: str
    voided_at: datetime
    void_reason: str
    replaced_by_number: str | None = None
    credit_note_number: str | None = None  # null when the voided invoice had no amount


class DueOut(Schema):
    id: UUID
    club: ClubRef
    plan: PlanRef
    cycle: CycleRef | None = None
    amount: Decimal  # NET, excluding VAT
    vat_rate: Decimal
    vat_amount: Decimal
    total: Decimal  # amount + vat_amount (gross); everything is settled against this
    reverse_charge: bool
    amount_paid: Decimal
    balance: Decimal  # total - amount_paid
    period_start: date
    period_end: date
    grace_until: date
    status: str  # unpaid | partial | paid | waived | cancelled
    state: str  # overdue | in_grace | issued_ahead | settled
    is_trial: bool
    paid_at: datetime | None = None
    invoice: InvoiceOut | None = None
    payments: list[PaymentOut] = []
    prorated: bool = False  # shortened and repriced by a plan change; amount/vat_amount/total/period_end are then the prorated values
    original_amount: Decimal | None = None  # net before proration
    original_vat_amount: Decimal | None = None
    original_total: Decimal | None = None
    original_period_end: date | None = None
    prorated_at: datetime | None = None
    credit_transferred: Decimal = Decimal("0")  # payments on this period that were moved to the club's credit (still listed in payments)
    credit_notes: list[CreditNoteBrief] = []
    voided_invoices: list[VoidedInvoiceOut] = []  # history of voided invoices of this period, newest first

    @staticmethod
    def of(due, *, with_payments: bool = False) -> DueOut:
        from billing.services.dues import today

        day = today()
        state = "overdue" if due.is_overdue(day) else "in_grace" if due.is_in_grace(day) else "issued_ahead" if due.is_issued_ahead(day) else "settled"
        invoices = list(due.invoices.all())
        invoice = next((i for i in invoices if i.voided_at is None), None)
        notes = sorted(due.credit_notes.all(), key=lambda n: n.created)
        void_notes = {n.invoice_id: n.number for n in notes if n.kind == "void"}
        by_id = {i.id: i for i in invoices}
        voided = [
            VoidedInvoiceOut(
                number=i.number,
                voided_at=i.voided_at,
                void_reason=i.void_reason,
                replaced_by_number=by_id[i.replaced_by_id].number if i.replaced_by_id in by_id else None,
                credit_note_number=void_notes.get(i.id),
            )
            for i in sorted((i for i in invoices if i.voided_at is not None), key=lambda i: i.voided_at, reverse=True)
        ]
        return DueOut(
            id=due.pk,
            club=ClubRef(id=due.club.pk, name=due.club.name, slug=due.club.slug),
            plan=PlanRef(id=due.plan.pk, code=due.plan.code, name=due.plan.name),
            cycle=CycleRef.of(due.cycle),
            amount=due.amount,
            vat_rate=due.vat_rate,
            vat_amount=due.vat_amount,
            total=due.total,
            reverse_charge=due.reverse_charge,
            amount_paid=due.amount_paid,
            balance=due.balance,
            period_start=due.period_start,
            period_end=due.period_end,
            grace_until=due.grace_until,
            status=due.status,
            state=state,
            is_trial=due.is_trial,
            paid_at=due.paid_at,
            invoice=InvoiceOut.of(invoice, siblings=invoices) if invoice else None,
            payments=[PaymentOut.of(p) for p in due.payments.select_related("recorded_by")] if with_payments else [],
            prorated=due.is_prorated,
            original_amount=due.original_amount,
            original_vat_amount=due.original_vat_amount,
            original_total=due.original_total,
            original_period_end=due.original_period_end,
            prorated_at=due.prorated_at,
            credit_transferred=due.credit_transferred,
            credit_notes=[CreditNoteBrief(number=n.number, total=n.total, kind=n.kind) for n in notes],
            voided_invoices=voided,
        )


class ClubBillingOut(Schema):
    billed: bool
    plan: PlanRef | None = None
    auto_renew: bool | None = None
    auto_archive: bool | None = None
    notes: str = ""
    cycle: CycleRef | None = None  # how the club is billed on its plan
    trial_ends_at: date | None = None
    downgraded_at: datetime | None = None  # set while the club is on the default plan because a period is overdue
    owed: Decimal
    dues: list[DueOut]
    credit_balance: Decimal = Decimal("0")  # credit held for the club; applied automatically to its next invoices
    credit_notes: list[CreditNoteOut] = []  # the latest few


class SubscribeBillingIn(Schema):
    plan_id: UUID
    start: date | None = None
    auto_renew: bool = True
    auto_archive: bool = True
    notes: str = ""
    trial_days: int | None = Field(None, ge=1, le=365)  # free trial of this many days; only for a club that is not billed yet
    cycle_id: UUID | None = None  # the billing cycle (monthly, yearly ...); omit for the plan's first active one
    invoice_number: str | None = None  # custom number for the first period's invoice (the trial's zero invoice too); omit for automatic


class PlanChangeIn(Schema):
    plan_id: UUID
    cycle_id: UUID | None = None  # the billing cycle on the new plan; omit for the plan's first active one (or, on the same plan, a different cycle is required)
    effective_date: date | None = None  # default today; between the day after the current period's start and today
    invoice_number: str | None = None  # custom number for the NEW period's invoice; omit for automatic


class MoneyOut(Schema):
    net: Decimal
    vat: Decimal
    total: Decimal

    @staticmethod
    def of(m) -> MoneyOut:
        return MoneyOut(net=m.net, vat=m.vat, total=m.total)


class OldPeriodOut(Schema):
    id: UUID
    period_start: date
    period_end: date  # as it was before the change
    new_period_end: date  # the day before the switch date
    total_days: int
    used_days: int
    unused_days: int
    original: MoneyOut
    prorated: MoneyOut  # equals original for a trial or waived period (only shortened)
    status_before: str
    status: str  # status after the change
    paid: Decimal  # recorded payments on the period (net of anything moved to credit earlier)
    credit_to_issue: MoneyOut | None = None  # the credit note (null: nothing to credit)
    overpayment_to_credit: Decimal  # paid beyond the prorated total: moved to the club's credit
    has_invoice: bool
    invoice_number: str | None = None
    is_trial: bool = False


class CancelledPeriodOut(Schema):
    id: UUID
    period_start: date
    period_end: date
    total: Decimal
    paid: Decimal  # becomes club credit
    invoice_number: str | None = None


class CreditNoteToIssueOut(Schema):
    for_invoice: str | None = None
    net: Decimal
    vat: Decimal
    total: Decimal


class NewPeriodOut(Schema):
    period_start: date
    period_end: date
    plan: PlanRef
    cycle: CycleRef | None = None
    net: Decimal
    vat_rate: Decimal
    vat_amount: Decimal
    total: Decimal
    reverse_charge: bool
    grace_until: date
    invoice_opens: str = "immediately"


class PlanChangePreviewOut(Schema):
    club_id: UUID
    effective_date: date
    from_plan: PlanRef | None = None
    to_plan: PlanRef
    from_cycle: CycleRef | None = None
    to_cycle: CycleRef | None = None
    old_period: OldPeriodOut | None = None  # null when no period covers the switch date
    cancelled_periods: list[CancelledPeriodOut]
    credit_notes_to_issue: list[CreditNoteToIssueOut]
    credit_balance_before: Decimal
    credit_available_for_new_period: Decimal  # balance before + credit created by this change
    new_period: NewPeriodOut
    credit_applied: Decimal
    amount_payable_after_credit: Decimal
    credit_balance_after: Decimal
    warnings: list[str]
    invoice_number: NumberEchoOut  # how the new period's invoice will be numbered

    @staticmethod
    def of(change) -> PlanChangePreviewOut:
        def ref(p):
            return PlanRef(id=p.pk, code=p.code, name=p.name)

        o = change.old
        old = None
        if o is not None:
            inv = o.invoice
            old = OldPeriodOut(
                id=o.due.pk,
                period_start=o.period_start,
                period_end=o.period_end,
                new_period_end=o.new_period_end,
                total_days=o.total_days,
                used_days=o.used_days,
                unused_days=o.unused_days,
                original=MoneyOut.of(o.original),
                prorated=MoneyOut.of(o.prorated),
                status_before=o.status_before,
                status=o.status_after,
                paid=o.paid,
                credit_to_issue=MoneyOut.of(o.credit_to_issue) if o.credit_to_issue else None,
                overpayment_to_credit=o.overpayment,
                has_invoice=inv is not None,
                invoice_number=inv.number if inv else None,
                is_trial=o.due.is_trial,
            )
        q = change.quote
        return PlanChangePreviewOut(
            club_id=change.club.pk,
            effective_date=change.effective_date,
            from_plan=ref(change.plan_from) if change.plan_from else None,
            to_plan=ref(change.plan_to),
            from_cycle=CycleRef.of(change.cycle_from),
            to_cycle=CycleRef.of(change.cycle_to),
            old_period=old,
            cancelled_periods=[CancelledPeriodOut(id=c.due.pk, period_start=c.due.period_start, period_end=c.due.period_end, total=c.total, paid=c.paid, invoice_number=c.invoice.number if c.invoice else None) for c in change.cancelled],
            credit_notes_to_issue=[CreditNoteToIssueOut(for_invoice=inv.number if inv else None, net=m.net, vat=m.vat, total=m.total) for inv, m, _due in change.credit_notes_to_issue],
            credit_balance_before=change.credit_balance_before,
            credit_available_for_new_period=change.credit_available,
            new_period=NewPeriodOut(
                period_start=q.start, period_end=q.end, plan=ref(q.plan), cycle=CycleRef.of(q.cycle), net=q.net, vat_rate=q.vat_rate, vat_amount=q.vat_amount, total=q.total, reverse_charge=q.reverse_charge, grace_until=q.grace_until
            ),
            credit_applied=change.credit_applied,
            amount_payable_after_credit=change.amount_payable,
            credit_balance_after=change.credit_balance_after,
            warnings=change.warnings,
            invoice_number=NumberEchoOut(mode="custom" if change.invoice_number else "automatic", number=change.invoice_number, next_automatic=change.next_automatic_number),
        )


class PlanChangeCreatedOut(Schema):
    old_due_id: UUID | None = None
    new_due_id: UUID
    new_invoice_id: UUID | None = None
    new_invoice_number: str | None = None
    credit_note_ids: list[UUID]
    cancelled_due_ids: list[UUID]


class PlanChangeOut(PlanChangePreviewOut):
    created: PlanChangeCreatedOut
    credit_notes: list[CreditNoteOut]
    billing: ClubBillingOut


class OpenPeriodIn(Schema):
    start: date | None = None
    invoice_number: str | None = None  # custom number for the new period's invoice; omit for automatic
    invoice: bool = True  # false: open the period only, create the invoice afterwards (POST /billing/dues/{id}/invoice); cannot be combined with invoice_number


class BillingSettingsIn(Schema):
    auto_renew: bool | None = None
    auto_archive: bool | None = None
    notes: str | None = None


class JobOut(Schema):
    id: str
    name: str
    status: str  # "success", "failure", "running", "pending", "retry", "revoked"
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    scheduled: bool  # started by celery beat rather than by a request
    worker: str | None
    detail: str  # the result on success, the last line of the traceback on failure


class JobsStatusOut(Schema):
    state: str  # "ok", "failing" (a failure inside the window) or "busy" (jobs running, none failed)
    failed: int
    running: int
    window_hours: int
    last_run_at: datetime | None


class JobDetailOut(JobOut):
    traceback: str | None
    result: str | None
    args: str | None
    kwargs: str | None
    periodic_task_name: str | None


class CronIn(Schema):
    minute: str
    hour: str
    day_of_month: str
    month_of_year: str
    day_of_week: str


class CronOut(CronIn):
    timezone: str | None = None


class JobRunBrief(Schema):
    status: str
    started_at: datetime | None
    duration_seconds: float | None
    detail: str


class PeriodicJobOut(Schema):
    name: str
    task: str
    label: str
    description: str
    builtin: bool  # celery's own housekeeping
    enabled: bool
    state: str  # "ok", "error", "running", "paused", "never"
    schedule: CronOut | None
    default_schedule: CronIn | None
    edited: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    total_run_count: int
    latest: JobRunBrief | None
    recent: list[str]  # statuses, oldest first


class JobEnabledIn(Schema):
    enabled: bool


class JobQueuedOut(Schema):
    task_id: str


class CountryOut(Schema):
    code: str
    name: str


from .user_schemas import *  # noqa: E402,F403  (user lookup schemas)


class HomeLocationIn(Schema):
    name: str
    address: str
    zip_code: str
    city: str
    country: str  # ISO 3166-1 alpha-2 code, e.g. "BE"
    latitude: float | None = None  # optional exact position; both or neither
    longitude: float | None = None


class HomeLocationOut(Schema):
    id: UUID
    name: str
    address: str
    zip_code: str
    city: str
    country: str
    country_name: str
    latitude: float | None = None
    longitude: float | None = None


class InvoicingNeedOut(Schema):
    """A club that needs an invoice made by hand (see billing.services.invoicing_mode.needs_invoice)."""

    kind: str  # open: the next period is not opened yet | invoice: the period exists but has no invoice
    club: ClubRef
    plan: PlanRef
    period_start: date
    opens_on: date  # when the club's invoicing window opened (period start minus the plan's renewal lead)
    days: int  # negative: the window opened that many days ago
    amount: str | None = None  # total incl. VAT of the period, when it can be quoted
    due_id: UUID | None = None  # the period, for kind=invoice


class InvoicingOut(Schema):
    auto_invoicing: bool
    pending_count: int  # clubs whose window has opened and that still need an invoice
    ahead_days: int
    items: list[InvoicingNeedOut]


class AutoInvoicingIn(Schema):
    enabled: bool
