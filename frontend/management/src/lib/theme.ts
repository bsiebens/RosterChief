export type Theme = 'auto' | 'light' | 'dark';
export const THEMES: Theme[] = ['auto', 'light', 'dark'];
const KEY = 'rc-theme';

/** The stored choice: `auto` follows the phone's light or dark setting. */
export function getTheme(): Theme {
	try {
		const value = localStorage.getItem(KEY);
		return value === 'light' || value === 'dark' ? value : 'auto';
	} catch {
		return 'auto';
	}
}

/** Store the choice and apply it at once (`data-theme` on <html>; none = follow the system). */
export function setTheme(theme: Theme): void {
	try {
		if (theme === 'auto') localStorage.removeItem(KEY);
		else localStorage.setItem(KEY, theme);
	} catch {
		/* private mode: the choice then lasts for this page only */
	}
	if (theme === 'auto') document.documentElement.removeAttribute('data-theme');
	else document.documentElement.setAttribute('data-theme', theme);
}
