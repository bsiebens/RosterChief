import type { ClubRef, PlanCycle, PlanRef } from '#lib/billing.ts';

export type Unbilled = { id: string; name: string; slug: string; plan_code: string | null };
export type PlanLite = { plan: PlanRef; is_active: boolean; position?: number; cycles: PlanCycle[] };
export type ClubTarget = ClubRef;
