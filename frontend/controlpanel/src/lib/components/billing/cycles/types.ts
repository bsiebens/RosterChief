import type { ClubRef, PlanRef } from '#lib/billing.ts';

export type Unbilled = { id: string; name: string; slug: string; plan_code: string | null };
export type PlanTerms = { duration_months: number; renewal_lead_days: number; grace_days: number };
export type PlanLite = { plan: PlanRef; is_active: boolean; terms: PlanTerms | null; current_price: string | null };
export type ClubTarget = ClubRef;
