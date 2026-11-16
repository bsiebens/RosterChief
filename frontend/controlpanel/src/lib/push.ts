export type ClubBrief = { id: string; name: string };

export type ProviderKey = 'apns' | 'fcm' | 'webpush' | 'email';
export type HealthState = 'ok' | 'degraded' | 'down' | 'quiet';

export type ProviderHealth = {
	provider: ProviderKey;
	label: string;
	configured: boolean | null;
	enabled: boolean | null;
	requests: number;
	sent: number;
	failed: number;
	dead: number;
	retrying: number;
	success_rate: number | null;
	p50_ms: number | null;
	p95_ms: number | null;
	last_success: string | null;
	last_failure: string | null;
	state: HealthState;
};

export type PushMonitoring = {
	window: string;
	since: string;
	club_id: string | null;
	alerts: { id: string; message: string; first_seen: string; notifications_sent: number }[];
	providers: ProviderHealth[];
	retry_queue: { waiting: number; oldest_next_attempt: string | null; overdue: number; stuck: boolean; reasons: string[] };
	outbox: { pending: number; failed: number; oldest_pending: string | null };
	series: { bucket: string; sent: number; not_sent: number }[];
	top_failures: { provider: string; error: string; count: number; last_seen: string | null }[];
	recent_problems: {
		id: string;
		created: string;
		provider: string;
		club: ClubBrief | null;
		target_masked: string;
		status: string;
		attempts: number;
		error: string;
		user_id?: string | null;
		user_email_masked: string | null;
	}[];
	devices: { ios: DeviceCounts; android: DeviceCounts; web: { subscriptions: number } };
	sandbox: { enabled: boolean; expires_at: string | null };
};
export type DeviceCounts = { enabled: number; disabled: number; stale: number };

export type PushConfigSettings = {
	apns_enabled: boolean;
	fcm_enabled: boolean;
	webpush_enabled: boolean;
	retry_delays_minutes: number[];
	outbox_max_attempts: number;
	sent_retention_days: number;
	failed_retention_days: number;
	stale_device_days: number;
	disabled_device_retention_days: number;
	unseen_device_retention_days: number;
	degraded_below_percent: number;
	down_below_percent: number;
	health_min_requests: number;
	alert_emails: string[] | string;
};
export type PushConfig = {
	providers: { provider: ProviderKey; label: string; configured: boolean; missing: string[]; enabled: boolean }[];
	settings: PushConfigSettings;
	sandbox_default: boolean;
};

export type SandboxState = { enabled: boolean | null; effective: boolean; expires_at: string | null; scenario: string; latency_ms: number; default_from_settings: boolean };
export type SandboxOverview = {
	state: SandboxState;
	scenarios: { value: string; label: string; description: string }[];
	events: { id: string; created: string; user_email: string | null; action: string; detail: unknown }[];
};
export type SandboxMessage = {
	id: string;
	created: string;
	channel: string;
	provider: string;
	club: ClubBrief | null;
	user_email: string | null;
	target: string;
	target_label: string;
	title: string;
	body: string;
	request: unknown;
	outcome: string;
};
export type SandboxMessages = { items: SandboxMessage[]; outcomes: Record<string, number>; providers: string[] };

export type SendResult = { channel: string; provider: string; target_masked: string; status: string; error: string; duration_ms: number | null };
export type SendResponse = { mode: string; sandboxed: boolean; results: SendResult[] };
export type PushTarget = { type: 'user' | 'device' | 'web_subscription'; id: string; label: string };

export type UserPush = {
	devices: {
		id: string;
		platform: string;
		platform_label: string;
		provider: string;
		environment: string;
		app_version: string;
		locale: string;
		enabled: boolean;
		disabled_at: string | null;
		last_seen: string | null;
		stale: boolean;
	}[];
	web_subscriptions: { id: string; club: ClubBrief | null; user_agent: string; endpoint_masked: string; last_seen: string | null }[];
	preferences: {
		email_mode: string;
		email_mode_label: string;
		quiet_hours: { enabled: boolean; start: string; end: string; timezone: string; active_now: boolean; until: string | null } | null;
		master_switches: { club: ClubBrief | null; channel: string; enabled: boolean }[];
		categories_off: { club: ClubBrief | null; category: string; category_label: string; channel: string }[];
	};
	recent_deliveries: { id: string; created: string; provider: string; status: string; category: string; title: string; attempts: number; error: string }[];
};

export const PROVIDER_LABEL: Record<string, string> = { apns: 'APNs (iOS)', fcm: 'FCM (Android)', webpush: 'Web push', email: 'Email' };
export const providerLabel = (p: string) => PROVIDER_LABEL[p] ?? p;

export const STATE_BADGE: Record<HealthState, string> = { ok: 'badge-success', degraded: 'badge-warning', down: 'badge-error', quiet: 'badge-ghost' };
export const STATUS_BADGE: Record<string, string> = { sent: 'badge-success', failed: 'badge-warning', retrying: 'badge-warning', dead: 'badge-error', held: 'badge-ghost' };

export const CATEGORIES: [string, string][] = [
	['general', 'General'],
	['news', 'News'],
	['event', 'Events'],
	['reminder', 'Reminders'],
	['lineup', 'Line-ups'],
	['crew', 'Crew'],
	['form', 'Forms'],
	['shop', 'Shop'],
	['coach', 'Coach'],
	['admin', 'Admin'],
	['announcement', 'Announcements'],
	['game', 'Game moments']
];

export const pct = (n: number | null) => (n === null || n === undefined ? '—' : `${n}%`);
export const ms = (n: number | null) => (n === null || n === undefined ? '—' : `${n} ms`);

export type UserFollowing = {
	clubs: { club: { id: string; name: string }; teams: { id: string; name: string }[]; games: { id: string; title: string; start: string }[] }[];
	total_teams: number;
	total_games: number;
};
