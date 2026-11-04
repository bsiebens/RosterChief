/** Colours for the jersey-number tiles (inline styles: independent of the club theme so the meaning stays constant). */
export type NumberStatus = 'free' | 'taken' | 'shared' | 'reserved' | 'conflict' | 'yours';
export const STATUS_ORDER: NumberStatus[] = ['free', 'taken', 'shared', 'reserved', 'conflict'];
export const TILE_STYLE: Record<NumberStatus, string> = {
	free: 'background:#ecfdf5;border-color:#6ee7b7;color:#065f46',
	taken: 'background:#e5e7eb;border-color:#9ca3af;color:#374151',
	shared: 'background:#e0f2fe;border-color:#7dd3fc;color:#075985',
	reserved: 'background:#fef3c7;border-color:#fbbf24;color:#92400e',
	conflict: 'background:#fee2e2;border-color:#f87171;color:#991b1b;font-weight:800',
	yours: 'background:var(--color-club);border-color:var(--color-club-dark);color:var(--color-club-content);font-weight:800'
};
