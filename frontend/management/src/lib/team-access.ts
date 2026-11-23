/**
 * The access token of a team's private page. The page asks for the emailed password once; the backend answers with a signed token that is
 * kept in a cookie for this link and sent as `X-Team-Access` with every call (server load functions read the cookie, the browser reads it
 * from `document.cookie`). The cookie is scoped to the tournament pages and lives as long as the token is valid.
 */
export const accessCookie = (token: string) => `tpa_${token.slice(0, 16).replace(/[^A-Za-z0-9_-]/g, '')}`;
const MAX_AGE = 14 * 24 * 3600;

export function storeAccess(token: string, access: string) {
	if (typeof document === 'undefined') return;
	document.cookie = `${accessCookie(token)}=${encodeURIComponent(access)}; path=/tournaments; max-age=${MAX_AGE}; samesite=lax${location.protocol === 'https:' ? '; secure' : ''}`;
}

export function readAccess(token: string): string {
	if (typeof document === 'undefined') return '';
	const name = `${accessCookie(token)}=`;
	const hit = document.cookie.split('; ').find((c) => c.startsWith(name));
	return hit ? decodeURIComponent(hit.slice(name.length)) : '';
}

export const accessHeaders = (token: string, value = readAccess(token)): Record<string, string> => (value ? { 'x-team-access': value } : {});
