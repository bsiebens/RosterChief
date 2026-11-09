/**
 * Documentation requirements (`/requirements/*`): what a club asks of its members (a medical certificate, a photo ...) and each
 * member's checklist per season. Managing needs `members.manage_members`; reading a member's checklist needs the right to see them.
 */
export const EVENT_KINDS = ['training', 'game', 'tournament', 'meeting', 'social', 'other'] as const;

export type Requirement = {
	id: string;
	name: string;
	description: string;
	requires_document: boolean;
	blocked_event_kinds: string[];
	is_active: boolean;
	/** Months a completion stays valid; null = never expires. */
	valid_for_months: number | null;
	/** Days before expiry that the member (or their guardians) is reminded. */
	reminder_days_before: number;
};
export type RequirementListItem = Requirement & { usage_count: number };

export type DocDisplay = 'missing' | 'complete' | 'expiring_soon' | 'expired' | 'bypassed';
export type ChecklistItem = {
	requirement: Requirement;
	/** An expired completion is `open` again. */
	state: 'open' | 'complete' | 'bypassed';
	display: DocDisplay;
	note: string;
	completed_at: string | null;
	completed_by: string | null;
	has_document: boolean;
	valid_until: string | null;
	days_left: number | null;
};
export type Checklist = {
	membership: { id: string; season: { id: string; name: string }; status: string; fee_status: string } | null;
	items: ChecklistItem[];
	total: number;
	done: number;
	open: number;
	expiring: number;
	expired: number;
};

export const documentHref = (membershipId: string, requirementId: string) => `/api/v1/requirements/memberships/${membershipId}/${requirementId}/document`;
