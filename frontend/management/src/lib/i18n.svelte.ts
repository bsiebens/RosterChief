/**
 * Tiny message catalog (en, nl, fr). Messages live in feature files `src/lib/messages/<namespace>.ts`:
 *
 *     export default { en: { 'members.title': 'Members' }, nl: { 'members.title': 'Leden' }, fr: { 'members.title': 'Membres' } };
 *
 * Every file under `messages/` is picked up automatically, so features never touch a shared index. Keys are
 * namespaced by feature (`shell.*`, `auth.*`, `members.*`). Use `t('key', { name })` with `{name}` placeholders.
 * Missing keys fall back to English, then to the key itself (visible in the UI, so gaps are easy to spot).
 *
 * The active language is set once per render from the root layout (`setLang(data.lang)`); the server resolves it
 * from the `rc_lang` cookie, then Accept-Language.
 */
export const LANGS = ['en', 'nl', 'fr'] as const;
export type Lang = (typeof LANGS)[number];
export const LANG_LABELS: Record<Lang, string> = { en: 'English', nl: 'Nederlands', fr: 'Français' };
export const LANG_COOKIE = 'rc_lang';

type Messages = Record<string, string>;
type Catalog = Record<Lang, Messages>;

const catalog: Catalog = { en: {}, nl: {}, fr: {} };
const files = import.meta.glob<{ default: Partial<Catalog> }>('./messages/*.ts', { eager: true });
for (const mod of Object.values(files)) for (const lang of LANGS) Object.assign(catalog[lang], mod.default[lang] ?? {});

const state = $state<{ lang: Lang }>({ lang: 'en' });

export const isLang = (v: unknown): v is Lang => typeof v === 'string' && (LANGS as readonly string[]).includes(v);
export const setLang = (lang: Lang) => (state.lang = lang);
export const getLang = () => state.lang;

export function t(key: string, params?: Record<string, string | number>): string {
	let msg = catalog[state.lang][key] ?? catalog.en[key] ?? key;
	if (params) for (const [k, v] of Object.entries(params)) msg = msg.replaceAll(`{${k}}`, String(v));
	return msg;
}

/** Pick the best supported language from an Accept-Language header. */
export function negotiate(header: string | null | undefined): Lang {
	for (const part of (header ?? '').split(',')) {
		const code = part.split(';')[0].trim().slice(0, 2).toLowerCase();
		if (isLang(code)) return code;
	}
	return 'en';
}
