/** Only ever follow same-site paths after sign-in, never an arbitrary URL from the query string. */
export function safeNext(raw: string | null | undefined): string {
	return raw && raw.startsWith('/') && !raw.startsWith('//') && !raw.startsWith('/\\') ? raw : '/';
}
