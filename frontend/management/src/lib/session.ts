import { goto, invalidateAll } from '$app/navigation';
import { auth } from './api.ts';
import type { Lang } from './i18n.svelte.ts';

/** End the session and return to the sign-in screen. */
export async function signOut() {
	await auth('DELETE', '/auth/session');
	await goto('/login', { refreshAll: true });
}

/** Remember the language in the rc_lang cookie, then reload the layout data so every string follows. */
export async function changeLang(lang: Lang) {
	await fetch('/lang', { method: 'POST', body: new URLSearchParams({ lang }) });
	await invalidateAll();
}

/**
 * Continue after a successful sign-in. On the bare domain the next page may redirect to another host (the user's
 * club), which needs a real navigation rather than a client-side one.
 */
export function afterSignIn(next: string, mode: string) {
	if (mode === 'platform') {
		window.location.assign(next);
		return Promise.resolve();
	}
	return goto(next, { refreshAll: true });
}
