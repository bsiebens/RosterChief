import type { RequestHandler } from './$types';

export const GET: RequestHandler = ({ locals }) => {
	const club = locals.branding;
	if (locals.mode !== 'club' || !club) return new Response(null, { status: 404 });
	return new Response(JSON.stringify({
		id: '/member',
		name: club.name + ' · RosterChief',
		short_name: club.name,
		start_url: '/app',
		scope: '/',
		display: 'standalone',
		theme_color: club.primary_color || '#0f1a2e',
		background_color: '#101318',
		icons: [192, 512].map((size) => ({
			src: (size === 192 ? club.icons.png_192 : club.icons.png_512) || `/images/app-icon-${size}.png`,
			sizes: `${size}x${size}`, type: 'image/png', purpose: 'any'
		}))
	}), { headers: { 'Content-Type': 'application/manifest+json', 'Cache-Control': 'public, max-age=300' } });
};
