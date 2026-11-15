/**
 * News (`/news/*`): the shapes the API returns and the small helpers the list, add and edit pages share.
 * Permission model (decided server-side, mirrored in the flags below): `news.publish_newsitems` edits everything and
 * publishes; anyone else adds news and edits / deletes only their own items while draft or pending review.
 */
import { isoToLocalInput } from '#lib/events/form-types.ts';

export type NewsStatus = 'draft' | 'pending_review' | 'published';
export type NewsVisibility = 'internal' | 'external' | 'both';
export const NEWS_STATUSES: NewsStatus[] = ['draft', 'pending_review', 'published'];
export const NEWS_VISIBILITIES: NewsVisibility[] = ['internal', 'external', 'both'];

export type NewsListItem = {
	id: string;
	title: string;
	slug: string;
	status: NewsStatus;
	visibility: NewsVisibility;
	published_at: string | null;
	is_scheduled: boolean;
	created: string;
	created_by: { id: string; name: string } | null;
	team_ids: string[];
	teams?: { id: string; name: string }[];
	main_photo_url: string | null;
	can_edit: boolean;
};
export type NewsPhoto = { id: string; url: string; is_main: boolean; ordering: number; focal_x: number; focal_y: number };
export type NewsDetail = NewsListItem & {
	body: string;
	title_en: string;
	body_en: string;
	body_html: string;
	notified_at: string | null;
	photos: NewsPhoto[];
	teams: { id: string; name: string }[];
	can_publish: boolean;
	can_delete: boolean;
};
export type NewsList = { count: number; results: NewsListItem[] };
export type NewsIn = { title: string; body: string; title_en: string; body_en: string; visibility: NewsVisibility; team_ids: string[] };
export type NewsPublishIn = { published_at: string | null; notify: boolean };
export type TeamOption = { id: string; name: string; short_name: string };

export const newsHref = (id: string) => `/news/${id}`;

/** A club wall-clock time ("YYYY-MM-DDTHH:MM", from a datetime-local input) as an ISO instant; '' when empty or invalid. */
export function wallToIso(local: string): string {
	const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(local);
	if (!m) return '';
	const want = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
	let guess = want;
	// The club's UTC offset depends on the date (daylight saving): correct the guess by what the club clock shows for it.
	for (let i = 0; i < 2; i++) {
		const shown = isoToLocalInput(new Date(guess).toISOString());
		const s = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(shown);
		if (!s) return '';
		guess += want - Date.UTC(+s[1], +s[2] - 1, +s[3], +s[4], +s[5]);
	}
	return new Date(guess).toISOString();
}

/** The editable fields of an item, as the form holds them. */
export const blankNews = (): NewsIn => ({ title: '', body: '', title_en: '', body_en: '', visibility: 'both', team_ids: [] });
export const newsToForm = (n: NewsDetail): NewsIn => ({ title: n.title, body: n.body, title_en: n.title_en, body_en: n.body_en, visibility: n.visibility, team_ids: [...n.team_ids] });

/** Badge key: a published item in the future shows as scheduled. */
export const statusKey = (n: Pick<NewsListItem, 'status' | 'is_scheduled'>): 'draft' | 'pending_review' | 'scheduled' | 'published' =>
	n.status === 'published' && n.is_scheduled ? 'scheduled' : n.status;

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function inline(raw: string): string {
	let s = esc(raw);
	s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
	s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
	return s;
}

/**
 * A small, safe approximation of the server's Markdown renderer (headings, bold, italic, links, lists, quotes) for the
 * live preview while typing (headings, bold, italic, links, lists, quotes and pipe tables). Input is HTML-escaped first and
 * only http(s) links survive. Other extras (fenced code, ...) show as plain text here; the saved preview (`body_html`) is
 * rendered by the server and is the real thing.
 */
const tableCells = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
const isTableSeparator = (line: string) => /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(line) && line.includes('|');

export function renderMarkdown(src: string): string {
	const out: string[] = [];
	let list: 'ul' | 'ol' | null = null;
	let para: string[] = [];
	const flush = () => {
		if (para.length) out.push(`<p>${para.map(inline).join('<br>')}</p>`);
		para = [];
	};
	const close = () => {
		if (list) out.push(`</${list}>`);
		list = null;
	};
	const lines = src.replace(/\r\n?/g, '\n').split('\n');
	for (let i = 0; i < lines.length; i++) {
		const line = lines[i];
		if (line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
			flush();
			close();
			const head = tableCells(line);
			const align = tableCells(lines[i + 1]).map((c) => (c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : c.startsWith(':') ? 'left' : ''));
			const cell = (tag: string, text: string, n: number) => `<${tag}${align[n] ? ` align="${align[n]}"` : ''}>${inline(text)}</${tag}>`;
			let html = `<table><thead><tr>${head.map((c, n) => cell('th', c, n)).join('')}</tr></thead><tbody>`;
			i += 2;
			while (i < lines.length && lines[i].trim() && lines[i].includes('|')) {
				html += `<tr>${tableCells(lines[i]).map((c, n) => cell('td', c, n)).join('')}</tr>`;
				i++;
			}
			i--;
			out.push(html + '</tbody></table>');
			continue;
		}
		const h = /^(#{1,4})\s+(.*)$/.exec(line);
		const ul = /^\s*[-*]\s+(.*)$/.exec(line);
		const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
		const q = /^>\s?(.*)$/.exec(line);
		if (!line.trim()) {
			flush();
			close();
		} else if (h) {
			flush();
			close();
			out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`);
		} else if (ul || ol) {
			flush();
			const kind = ul ? 'ul' : 'ol';
			if (list !== kind) {
				close();
				out.push(`<${kind}>`);
				list = kind;
			}
			out.push(`<li>${inline((ul ?? ol)![1])}</li>`);
		} else if (q) {
			flush();
			close();
			out.push(`<blockquote>${inline(q[1])}</blockquote>`);
		} else {
			close();
			para.push(line);
		}
	}
	flush();
	close();
	return out.join('');
}
