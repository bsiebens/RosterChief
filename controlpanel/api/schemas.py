from datetime import datetime
from uuid import UUID

from ninja import Schema


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


class PlanOut(Schema):
    id: UUID
    code: str
    name: str
    description: str
    is_active: bool
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
    source: str  # plan | default_plan | override | none


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


class SportIn(Schema):
    name: str


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


class SwitchesOut(Schema):
    maintenance: MaintenanceState
    email_suppression: SwitchState


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


class TermsIO(Schema):
    duration_months: int = 12
    renewal_lead_days: int = 30
    grace_days: int = 30
    is_trial: bool = False


class PriceIn(Schema):
    active_from: date
    amount: Decimal


class PriceOut(Schema):
    id: UUID
    active_from: date
    amount: Decimal


class BillingPlanOut(Schema):
    plan: PlanRef
    is_active: bool
    terms: TermsIO
    prices: list[PriceOut]
    current_price: Decimal | None = None  # None = cannot be billed (no price yet)
    club_count: int

    @staticmethod
    def of(plan) -> BillingPlanOut:
        from billing.services.dues import price_on, today
        from features.models import Subscription

        t = plan.terms
        price = price_on(plan, today())
        return BillingPlanOut(
            plan=PlanRef(id=plan.pk, code=plan.code, name=plan.name),
            is_active=plan.is_active,
            terms=TermsIO(duration_months=t.duration_months, renewal_lead_days=t.renewal_lead_days, grace_days=t.grace_days, is_trial=t.is_trial),
            prices=[PriceOut(id=p.pk, active_from=p.active_from, amount=p.amount) for p in plan.prices.all()],
            current_price=price.amount if price else None,
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
    needs_sending: bool

    @staticmethod
    def of(inv) -> InvoiceOut:
        return InvoiceOut(number=inv.number, issued_at=inv.issued_at, sent_at=inv.sent_at, sent_method=inv.sent_method, needs_sending=inv.needs_sending)


class ClubRef(Schema):
    id: UUID
    name: str
    slug: str


class DueOut(Schema):
    id: UUID
    club: ClubRef
    plan: PlanRef
    amount: Decimal
    amount_paid: Decimal
    balance: Decimal
    period_start: date
    period_end: date
    grace_until: date
    status: str  # unpaid | partial | paid | waived | cancelled
    state: str  # overdue | in_grace | issued_ahead | settled
    is_trial: bool
    paid_at: datetime | None = None
    invoice: InvoiceOut | None = None
    payments: list[PaymentOut] = []

    @staticmethod
    def of(due, *, with_payments: bool = False) -> DueOut:
        from billing.services.dues import today

        day = today()
        state = "overdue" if due.is_overdue(day) else "in_grace" if due.is_in_grace(day) else "issued_ahead" if due.is_issued_ahead(day) else "settled"
        invoice = getattr(due, "invoice", None)
        return DueOut(
            id=due.pk,
            club=ClubRef(id=due.club.pk, name=due.club.name, slug=due.club.slug),
            plan=PlanRef(id=due.plan.pk, code=due.plan.code, name=due.plan.name),
            amount=due.amount,
            amount_paid=due.amount_paid,
            balance=due.balance,
            period_start=due.period_start,
            period_end=due.period_end,
            grace_until=due.grace_until,
            status=due.status,
            state=state,
            is_trial=due.is_trial,
            paid_at=due.paid_at,
            invoice=InvoiceOut.of(invoice) if invoice else None,
            payments=[PaymentOut.of(p) for p in due.payments.select_related("recorded_by")] if with_payments else [],
        )


class ClubBillingOut(Schema):
    billed: bool
    plan: PlanRef | None = None
    auto_renew: bool | None = None
    auto_archive: bool | None = None
    notes: str = ""
    trial_ends_at: date | None = None
    post_trial_plan: PlanRef | None = None
    owed: Decimal
    dues: list[DueOut]


class SubscribeBillingIn(Schema):
    plan_id: UUID
    start: date | None = None
    auto_renew: bool = True
    auto_archive: bool = True
    notes: str = ""


class TrialIn(Schema):
    trial_plan_id: UUID
    post_trial_plan_id: UUID
    start: date | None = None
    auto_renew: bool = True
    auto_archive: bool = True


class OpenPeriodIn(Schema):
    start: date | None = None


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
