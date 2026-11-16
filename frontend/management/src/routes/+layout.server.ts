import type { LayoutServerLoad } from './$types';

/** Root contract: every page gets `auth`, `lang`, `mode` ('platform' | 'club' | 'error') and `branding` (club mode). */
export const load: LayoutServerLoad = ({ locals }) => ({
	auth: locals.auth,
	lang: locals.lang,
	mode: locals.mode,
	branding: locals.branding
});
