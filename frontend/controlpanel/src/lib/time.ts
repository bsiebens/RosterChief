/** The browser's timezone (IANA name, e.g. "Europe/Brussels"); times in the control panel are shown in it. */
export const browserZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

/** Short zone label for the browser right now, e.g. "CET" or "GMT+1". */
export const zoneLabel = () => new Intl.DateTimeFormat('en-GB', { timeZoneName: 'short' }).formatToParts(new Date()).find((p) => p.type === 'timeZoneName')?.value ?? '';

/** "02 Nov, 05:00 CET" -- always with the zone, so a time can't be mistaken for server (UTC) time. */
export const withZone = (iso: string | null, seconds = false) =>
	iso
		? new Date(iso).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}), timeZoneName: 'short' })
		: '—';

/** "UTC" for a cron schedule's zone name; other zones are left as they are. */
export const scheduleZone = (tz: string | null | undefined) => (!tz ? '' : /^(utc|etc\/utc)$/i.test(tz) ? 'UTC' : tz);
