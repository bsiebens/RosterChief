import { t } from '#lib/i18n.svelte.ts';

/** The small status pill of the design boards (Going, Reply, Maybe, Can't ...), as classes for the app's tokens. */
const BASE = 'inline-flex h-6 shrink-0 items-center rounded-full px-2.5 text-[13px] font-bold whitespace-nowrap';
const TONE = {
	ok: 'bg-ok-bg text-ok-text',
	red: 'bg-danger-bg text-danger',
	muted: 'bg-subhead text-muted'
};

export function statusPill(status: string): { cls: string; text: string } {
	switch (status) {
		case 'present':
			return { cls: `${BASE} ${TONE.ok}`, text: t('portal.pill_going') };
		case 'selected':
			return { cls: `${BASE} ${TONE.ok}`, text: t('portal.pill_selected') };
		case 'no_response':
			return { cls: `${BASE} ${TONE.red}`, text: t('portal.pill_reply') };
		case 'maybe':
			return { cls: `${BASE} ${TONE.muted}`, text: t('portal.pill_maybe') };
		case 'not_selected':
			return { cls: `${BASE} ${TONE.muted}`, text: t('portal.pill_not_selected') };
		case 'duty':
			return { cls: `${BASE} ${TONE.ok}`, text: t('portal.pill_duty') };
		default:
			return { cls: `${BASE} ${TONE.muted}`, text: t('portal.pill_cant') };
	}
}
