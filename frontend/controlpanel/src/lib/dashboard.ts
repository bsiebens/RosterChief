export type Metric = { label: string; value: number | string };

export type ClubRow = {
	club_id: string;
	name: string;
	slug: string;
	plan: string | null;
	members: number | null;
	member_limit: number | null;
	dues_owed: string;
	grace_until: string | null;
	risk: 'high' | 'watch' | 'ok';
	reason: string;
};

export type Series = { month: string; value: string }[];

export type Dashboard = {
	generated_at: string;
	totals: Record<string, Metric>;
	attention: Record<string, Metric>;
	funnel: { key: string; step: string; count: number }[];
	plans: { plan: string; clubs: number }[];
	features: { code: string; name: string; clubs: number; overridden: number }[];
	charts: { new_clubs: Series; payments_received_eur: Series };
	clubs: ClubRow[];
};

const eur = new Intl.NumberFormat('en-IE', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
export const money = (v: string | number | undefined) => eur.format(Number(v ?? 0));
export const num = (v: string | number | undefined) => Number(v ?? 0);

/** Attention metrics that are a problem right now (red) as opposed to a nudge (amber), and what to do about each. */
export const ALERTS: Record<string, { tone: 'club' | 'warn'; hint: string; money?: boolean }> = {
	dues_overdue: { tone: 'club', hint: 'Past their grace period. See billing.' },
	renewals_pending: { tone: 'club', hint: 'renew_subscriptions may be stalled.' },
	failed_jobs_24h: { tone: 'club', hint: 'Check the job log.' },
	club_creations_failed: { tone: 'club', hint: 'Club provisioning did not finish.' },
	invoices_to_send: { tone: 'warn', hint: 'Waiting to be sent from billing.' },
	admins_pending_mfa: { tone: 'warn', hint: 'Locked out until they enrol.' },
	clubs_unbilled: { tone: 'warn', hint: 'Using the platform for free.' },
	clubs_near_member_limit: { tone: 'warn', hint: 'Candidates for an upgrade.' },
	dues_in_grace: { tone: 'warn', hint: 'Still inside the grace period.' }
};

export type Job = {
	id: string;
	name: string;
	status: 'success' | 'failure' | 'running' | 'pending' | 'retry' | 'revoked';
	started_at: string | null;
	finished_at: string | null;
	duration_seconds: number | null;
	scheduled: boolean;
	worker: string | null;
	detail: string;
};

export type JobsStatus = {
	state: 'ok' | 'failing' | 'busy';
	failed: number;
	running: number;
	window_hours: number;
	last_run_at: string | null;
};

export type JobDetail = Job & {
	traceback: string | null;
	result: string | null;
	args: string | null;
	kwargs: string | null;
	periodic_task_name: string | null;
};

export type Cron = { minute: string; hour: string; day_of_month: string; month_of_year: string; day_of_week: string };

export type PeriodicJob = {
	name: string;
	task: string;
	label: string;
	description: string;
	builtin: boolean;
	enabled: boolean;
	state: 'ok' | 'error' | 'running' | 'paused' | 'never';
	schedule: (Cron & { timezone: string | null }) | null;
	default_schedule: Cron | null;
	edited: boolean;
	next_run_at: string | null;
	last_run_at: string | null;
	total_run_count: number;
	latest: { status: string; started_at: string | null; duration_seconds: number | null; detail: string } | null;
	recent: string[];
};
