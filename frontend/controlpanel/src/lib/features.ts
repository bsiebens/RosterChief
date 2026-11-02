export type SwitchState = { is_active: boolean; started_at: string | null; started_by_email: string | null };
export type Switches = { maintenance: SwitchState & { message: string }; email_suppression: SwitchState };

export type CatalogFeature = { code: string; name: string; description: string; kind: 'flag' | 'limit'; limit_scope: string | null; is_active: boolean };

export type PlanFeature = { code: string; name: string; kind: 'flag' | 'limit'; limit: number | null };
export type FullPlan = { id: string; code: string; name: string; description: string; is_active: boolean; is_default: boolean; subscriber_count: number; features: PlanFeature[] };

export type LogEntry = {
	id: string;
	created: string;
	action: string;
	club_id: string | null;
	plan_code: string | null;
	feature_code: string | null;
	actor_email: string | null;
	old: Record<string, unknown> | null;
	new: Record<string, unknown> | null;
	reason: string;
};

export type ClubOption = { id: string; name: string };
export const limitText = (f: PlanFeature) => (f.kind === 'limit' ? (f.limit == null ? 'unlimited' : String(f.limit)) : 'included');
