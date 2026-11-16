import type { Cron } from './dashboard.ts';

const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
export const DAY_NAMES = DAYS;

const isNum = (v: string) => /^\d+$/.test(v);
const pad = (v: string | number) => String(v).padStart(2, '0');
export const raw = (c: Cron) => `${c.minute} ${c.hour} ${c.day_of_month} ${c.month_of_year} ${c.day_of_week}`;

/** A readable version of the cron shapes people actually use; anything else falls back to the raw expression. */
export function describe(c: Cron): string {
	const { minute, hour, day_of_month: dom, month_of_year: moy, day_of_week: dow } = c;
	const restStar = (...v: string[]) => v.every((x) => x === '*');
	if (restStar(minute, hour, dom, moy, dow)) return 'Every minute';
	const step = minute.match(/^\*\/(\d+)$/);
	if (step && restStar(hour, dom, moy, dow)) return `Every ${step[1]} minutes`;
	if (isNum(minute) && restStar(hour, dom, moy, dow)) return `Hourly, at :${pad(minute)}`;
	if (isNum(minute) && isNum(hour) && moy === '*') {
		const at = `${pad(hour)}:${pad(minute)}`;
		if (dom === '*' && dow === '*') return `Daily at ${at}`;
		if (dom === '*' && isNum(dow)) return `Every ${DAYS[Number(dow) % 7]} at ${at}`;
		if (isNum(dom) && dow === '*') return `Monthly, on day ${dom} at ${at}`;
	}
	return raw(c);
}

export type Preset = 'minutes' | 'hourly' | 'daily' | 'weekly' | 'monthly' | 'custom';

/** Which preset a schedule belongs to, plus the values that preset's inputs start with. */
export function detect(c: Cron): { preset: Preset; every: string; minute: string; time: string; weekday: string; day: string } {
	const base = { every: '5', minute: '0', time: '04:00', weekday: '1', day: '1' };
	const { minute, hour, day_of_month: dom, month_of_year: moy, day_of_week: dow } = c;
	const star = (...v: string[]) => v.every((x) => x === '*');
	const step = minute.match(/^\*\/(\d+)$/);
	if (step && star(hour, dom, moy, dow)) return { ...base, preset: 'minutes', every: step[1] };
	if (star(minute, hour, dom, moy, dow)) return { ...base, preset: 'minutes', every: '1' };
	if (isNum(minute) && star(hour, dom, moy, dow)) return { ...base, preset: 'hourly', minute };
	if (isNum(minute) && isNum(hour) && moy === '*') {
		const time = `${pad(hour)}:${pad(minute)}`;
		if (dom === '*' && dow === '*') return { ...base, preset: 'daily', time };
		if (dom === '*' && isNum(dow)) return { ...base, preset: 'weekly', time, weekday: dow };
		if (isNum(dom) && dow === '*') return { ...base, preset: 'monthly', time, day: dom };
	}
	return { ...base, preset: 'custom' };
}

export function build(p: { preset: Preset; every: string; minute: string; time: string; weekday: string; day: string }, current: Cron): Cron {
	const [h, m] = p.time.split(':').map((x) => String(Number(x)));
	switch (p.preset) {
		case 'minutes':
			return { minute: p.every === '1' ? '*' : `*/${Number(p.every)}`, hour: '*', day_of_month: '*', month_of_year: '*', day_of_week: '*' };
		case 'hourly':
			return { minute: String(Number(p.minute)), hour: '*', day_of_month: '*', month_of_year: '*', day_of_week: '*' };
		case 'daily':
			return { minute: m, hour: h, day_of_month: '*', month_of_year: '*', day_of_week: '*' };
		case 'weekly':
			return { minute: m, hour: h, day_of_month: '*', month_of_year: '*', day_of_week: p.weekday };
		case 'monthly':
			return { minute: m, hour: h, day_of_month: String(Number(p.day)), month_of_year: '*', day_of_week: '*' };
		default:
			return current;
	}
}
