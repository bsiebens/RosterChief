/**
 * A PDF's HTML shown in an iframe: the printed page has white margins around the content, so the preview gets the same
 * instead of running up to the frame's border.
 */
const MARGINS = '<style>html{background:#fff}body{margin:0!important;padding:2.5rem 3rem!important;box-sizing:border-box}</style>';

export function withPageMargins(html: string): string {
	return /<\/head>/i.test(html) ? html.replace(/<\/head>/i, `${MARGINS}</head>`) : MARGINS + html;
}
