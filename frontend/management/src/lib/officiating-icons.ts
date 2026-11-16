import { ClipboardList, Whistle } from '@lucide/svelte';

/** One icon per kind, used wherever referees and officials appear: member page, team page, lists. */
export const kindIcon = { referee: Whistle, official: ClipboardList } as const;
