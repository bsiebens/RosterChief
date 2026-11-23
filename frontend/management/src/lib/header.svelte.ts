import { onDestroy } from 'svelte';
import type { Snippet } from 'svelte';

/**
 * The shell's sticky top bar (page title, mono context line, action buttons) is filled by the page that is showing.
 * The (app) layout renders `header`; pages call `usePageHeader(() => ({ title, context, actions }))` in their script.
 * `actions` is a snippet (buttons) rendered right-aligned in the bar. The header is cleared when the page is destroyed.
 */
/** A link back to the parent list, shown next to the bell (set by a layout, not by each page). */
export type HeaderBack = { href: string; label: string };
export type PageHeader = { title: string; context?: string; /** A pill shown right behind the context line. */ badge?: Snippet | null; actions?: Snippet | null };

const fresh = $state<PageHeader>({ title: '', context: '', badge: null, actions: null });
const freshBack = $state<{ link: HeaderBack | null }>({ link: null });
/** Who filled the bar last: a page that is destroyed after the next page has set its own header must not clear it. */
const freshShared: { owner: symbol | null } = { owner: null };

// One instance for the whole app: the bundler can load this module twice (the layout and some pages ended up with their own copy, so a
// page filled a bar nobody drew). Pinning the state on globalThis makes every copy share it.
const g = globalThis as unknown as { __rcHeader?: { header: PageHeader; back: { link: HeaderBack | null }; shared: { owner: symbol | null } } };
const store = (g.__rcHeader ??= { header: fresh, back: freshBack, shared: freshShared });

export const header = store.header;
export const headerBack = store.back;
const shared = store.shared;

export function usePageHeader(get: () => PageHeader) {
	const me = Symbol('page-header');
	$effect(() => {
		// While navigating away, `data` briefly belongs to the next page: reading the old page's fields can throw. Keep the
		// previous header then, the next page sets its own.
		let h: PageHeader;
		try {
			h = get();
		} catch {
			return;
		}
		shared.owner = me;
		header.title = h.title;
		header.context = h.context ?? '';
		header.badge = h.badge ?? null;
		header.actions = h.actions ?? null;
	});
	onDestroy(() => {
		if (shared.owner !== me) return;
		shared.owner = null;
		header.title = '';
		header.context = '';
		header.badge = null;
		header.actions = null;
	});
}
