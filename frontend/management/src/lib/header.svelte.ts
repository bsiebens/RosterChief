import { onDestroy } from 'svelte';
import type { Snippet } from 'svelte';

/**
 * The shell's sticky top bar (page title, mono context line, action buttons) is filled by the page that is showing.
 * The (app) layout renders `header`; pages call `usePageHeader(() => ({ title, context, actions }))` in their script.
 * `actions` is a snippet (buttons) rendered right-aligned in the bar. The header is cleared when the page is destroyed.
 */
export type PageHeader = { title: string; context?: string; /** A pill shown right behind the context line. */ badge?: Snippet | null; actions?: Snippet | null };

export const header = $state<PageHeader>({ title: '', context: '', badge: null, actions: null });

export function usePageHeader(get: () => PageHeader) {
	$effect(() => {
		// While navigating away, `data` briefly belongs to the next page: reading the old page's fields can throw. Keep the
		// previous header then, the next page sets its own.
		let h: PageHeader;
		try {
			h = get();
		} catch {
			return;
		}
		header.title = h.title;
		header.context = h.context ?? '';
		header.badge = h.badge ?? null;
		header.actions = h.actions ?? null;
	});
	onDestroy(() => {
		header.title = '';
		header.context = '';
		header.badge = null;
		header.actions = null;
	});
}
