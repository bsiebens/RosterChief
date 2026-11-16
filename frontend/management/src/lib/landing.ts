/** Which app a person with both a member side and a staff side last used ("member" or "manage"): it decides where `/` opens next time. */
export const APP_COOKIE = 'rc_app';
export type AppChoice = 'member' | 'manage';

export function rememberApp(app: AppChoice) {
	try {
		document.cookie = `${APP_COOKIE}=${app}; path=/; max-age=31536000; samesite=lax`;
	} catch {
		// cookies blocked: the default landing applies
	}
}

/** True on a phone-sized screen (below the tablet breakpoint). */
export const isPhone = () => typeof matchMedia === 'function' && matchMedia('(max-width: 47.99rem)').matches;
