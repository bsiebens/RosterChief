export type PlatformAdmin = {
	id: string;
	email: string;
	first_name: string;
	last_name: string;
	is_staff: boolean;
	is_superuser: boolean;
	mfa_enabled: boolean;
};

export type AuditEntry = {
	id: string;
	created: string;
	action: string;
	actor_id: string | null;
	actor_email: string | null;
	target_type: string;
	target_id: string;
	club_id: string | null;
	data: Record<string, unknown>;
};

export type AuditQuery = { action: string; actor: string; club: string; target: string; page: number };

export const ACTIONS = [
	'platform_admin.granted',
	'platform_admin.updated',
	'platform_admin.revoked',
	'club.created',
	'club.create_failed',
	'club.updated',
	'club.archived',
	'club.restored',
	'club.deleted',
	'club.admin_added',
	'club.admin_removed',
	'billing.subscribed',
	'billing.period_opened',
	'billing.period_renewed',
	'billing.price_added',
	'billing.settings_updated',
	'billing.terms_updated',
	'billing.invoice_issued',
	'billing.invoice_sent',
	'billing.invoice_marked_sent',
	'billing.payment_recorded',
	'billing.payment_removed',
	'billing.due_waived',
	'billing.plan_changed',
	'billing.issuer_settings_updated',
	'billing.club_downgraded',
	'billing.club_restored_after_payment',
	'billing.club_archived_overdue',
	'sport.created',
	'sport.renamed',
	'sport.deleted',
	'competition.created',
	'competition.updated',
	'competition.deleted',
	'referee_type.created',
	'referee_type.renamed',
	'referee_type.reordered',
	'referee_type.deleted',
	'job.paused',
	'job.resumed',
	'job.run_now',
	'job.schedule_changed',
	'job.schedule_reset',
	'switch.email_paused',
	'switch.email_resumed',
	'switch.maintenance_on',
	'switch.maintenance_off'
] as const;

export const TARGET_TYPES = ['club', 'user', 'sport', 'competition', 'refereetype', 'plan', 'due', 'invoice', 'periodictask'];

const NOUN: Record<string, string> = {
	platform_admin: 'platform admin',
	club: 'club',
	billing: 'billing',
	sport: 'sport',
	competition: 'competition',
	referee_type: 'referee type',
	job: 'job',
	switch: 'switch'
};

const SPECIAL: Record<string, string> = {
	'club.admin_added': 'Added club administrator',
	'club.admin_removed': 'Removed club administrator',
	'club.create_failed': 'Club creation failed',
	'platform_admin.granted': 'Granted platform access',
	'platform_admin.updated': 'Changed platform level',
	'platform_admin.revoked': 'Revoked platform access',
	'billing.club_archived_overdue': 'Archived club for overdue dues',
	'billing.plan_changed': 'Switched plan and started a new billing cycle',
	'billing.issuer_settings_updated': 'Updated invoicing settings',
	'billing.invoice_issued': 'Issued invoice',
	'job.run_now': 'Ran job now',
	'switch.email_paused': 'Paused outgoing email',
	'switch.email_resumed': 'Resumed outgoing email',
	'switch.maintenance_on': 'Turned maintenance mode on',
	'switch.maintenance_off': 'Turned maintenance mode off'
};

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** 'club.archived' -> 'Archived club'; unknown actions fall back to a readable form. */
export function actionLabel(action: string): string {
	if (SPECIAL[action]) return SPECIAL[action];
	const [area, verb = ''] = action.split('.');
	const v = verb.replaceAll('_', ' ');
	const noun = NOUN[area] ?? area.replaceAll('_', ' ');
	if (area === 'billing') return cap(v.replace(/^(\w+) (\w+)$/, '$1 $2'));
	return `${cap(v)} ${noun}`;
}

export const adminName = (a: Pick<PlatformAdmin, 'first_name' | 'last_name' | 'email'>) => `${a.first_name} ${a.last_name}`.trim() || a.email;
