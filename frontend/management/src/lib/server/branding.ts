import type { Branding, MyClub } from '#lib/types.ts';

/** Only plain #RRGGBB values ever reach a style attribute. Anything else is dropped. */
export const safeColor = (v: unknown): string => (typeof v === 'string' && /^#[0-9a-fA-F]{6}$/.test(v) ? v : '');

/** Logos are site-relative /media/... URLs served by our proxy; refuse anything else (no external or script URLs). */
export const safeLogo = (v: unknown): string | null => (typeof v === 'string' && /^\/media\/[^\s"'<>()\\]+$/.test(v) ? v : null);

const ICON_KEYS = ['ico', 'png_32', 'png_192', 'png_512', 'apple_touch'] as const;

/** Generated icon URLs: same restriction as logos (site-relative /media/...), unknown keys dropped. */
function cleanIcons(raw: any): Branding['icons'] {
	const out: Branding['icons'] = {};
	for (const key of ICON_KEYS) {
		const url = safeLogo(raw?.[key]);
		if (url) out[key] = url;
	}
	return out;
}

export function cleanBranding(raw: any): Branding {
	return {
		slug: String(raw?.slug ?? ''),
		name: String(raw?.name ?? ''),
		is_platform: raw?.is_platform === true,
		logo_url: safeLogo(raw?.logo_url),
		icons: cleanIcons(raw?.icons),
		primary_color: safeColor(raw?.primary_color),
		secondary_color: safeColor(raw?.secondary_color),
		primary_content_color: safeColor(raw?.primary_content_color),
		secondary_content_color: safeColor(raw?.secondary_content_color)
	};
}

export function cleanMyClub(raw: any, url: string): MyClub {
	return {
		slug: String(raw?.slug ?? ''),
		name: String(raw?.name ?? ''),
		host: String(raw?.host ?? ''),
		logo_url: safeLogo(raw?.logo_url),
		primary_color: safeColor(raw?.primary_color),
		primary_content_color: safeColor(raw?.primary_content_color),
		url
	};
}

/** The club's colours as CSS custom properties for <html style>. layout.css derives the rest with color-mix. */
export function brandStyle(b: Branding | null): string {
	if (!b || b.is_platform) return '';
	const css: string[] = [];
	if (b.secondary_color) {
		css.push(`--tenant-club:${b.secondary_color}`);
		css.push(`--tenant-club-dark:color-mix(in srgb, ${b.secondary_color} 80%, black)`);
		if (b.secondary_content_color) css.push(`--tenant-club-content:${b.secondary_content_color}`);
	}
	if (b.primary_color) {
		css.push(`--tenant-sidebar-bg:${b.primary_color}`);
		if (b.primary_content_color) css.push(`--tenant-sidebar-fg:${b.primary_content_color}`);
	}
	return css.join(';');
}

/** Absolute URL of a club from the bare domain: the current request's scheme and port, the club's hostname. */
export function clubUrl(current: URL, host: string): string {
	const hostname = host.replace(/:\d+$/, '').replace(/[^a-zA-Z0-9.-]/g, '');
	return `${current.protocol}//${hostname}${current.port ? `:${current.port}` : ''}/`;
}

const DEFAULT_FAVICON = '<link rel="icon" href="/images/rosterchief-dark.svg" />';

/** The icon <link> tags: the set the backend generated from the club's logo (.ico, 32 px PNG, apple-touch-icon),
 * else the raw logo, else the RosterChief mark. URLs are restricted to /media/... by safeLogo. */
export function faviconTag(b: Branding | null): string {
	if (!b || b.is_platform) return DEFAULT_FAVICON;
	const { ico, png_32, apple_touch } = b.icons;
	if (png_32 || ico) {
		return [
			png_32 && `<link rel="icon" type="image/png" sizes="32x32" href="${png_32}" />`,
			ico && `<link rel="icon" sizes="any" href="${ico}" />`,
			apple_touch && `<link rel="apple-touch-icon" href="${apple_touch}" />`
		]
			.filter(Boolean)
			.join('\n\t\t');
	}
	return b.logo_url ? `<link rel="icon" href="${b.logo_url}" />` : DEFAULT_FAVICON;
}
