import { randomBytes } from 'node:crypto';

/**
 * Carries a signed-in session from the bare domain (club picker) to a club host without a second login.
 *
 * The session cookie is host-only in development (and per-host whenever the base domain differs), so a browser
 * arriving at the club's host has no cookie. The picker therefore sends it through `/go/<slug>`, which stores the
 * session token here under a random one-time code and redirects to `https://<club host>/handoff?code=...`; that
 * host redeems the code and sets its own cookie. The token itself never appears in a URL.
 *
 * A code is: single use, bound to the target hostname, valid for 30 seconds. It lives in this process's memory,
 * which is right for a single adapter-node instance; run several instances and this needs a shared store.
 */
const TTL_MS = 30_000;
const MAX_PENDING = 1000;
const pending = new Map<string, { token: string; host: string; expires: number }>();

function sweep() {
	const now = Date.now();
	for (const [code, h] of pending) if (h.expires <= now) pending.delete(code);
}

export function issueHandoff(token: string, host: string): string {
	sweep();
	if (pending.size >= MAX_PENDING) pending.clear(); // bounded memory; callers just retry
	const code = randomBytes(24).toString('base64url');
	pending.set(code, { token, host: host.toLowerCase(), expires: Date.now() + TTL_MS });
	return code;
}

/** The session token for `code` if it is still valid for `host`. Always consumes the code. */
export function redeemHandoff(code: string, host: string): string | null {
	const h = pending.get(code);
	pending.delete(code);
	if (!h || h.expires <= Date.now() || h.host !== host.toLowerCase()) return null;
	return h.token;
}
