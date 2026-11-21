<script lang="ts">
	import { t } from '#lib/i18n.svelte.ts';
	import { fmtDateRange } from '#lib/tournaments.svelte.ts';
	import type { TournamentForm } from '#lib/tournaments-form.ts';

	let { v, teams = [] }: { v: TournamentForm; teams?: { id: string; name: string }[] } = $props();
	const home = $derived(teams.find((team) => team.id === v.home_team_id)?.name ?? '');
	const dash = '—';
	const dates = $derived(v.days.map((d) => d.date).filter(Boolean).sort());
	const first = $derived(dates[0] ?? '');
	const last = $derived(dates[dates.length - 1] ?? '');
</script>

<dl class="grid gap-1.5 text-sm">
	<div class="flex justify-between gap-3"><dt class="text-muted">{t('tn.f.name')}</dt><dd class="text-right font-semibold">{v.name || dash}</dd></div>
	<div class="flex justify-between gap-3"><dt class="text-muted">{t('tn.col.dates')}</dt><dd class="font-mono text-xs">{first && last ? fmtDateRange(first, last) : dash}</dd></div>
	<div class="flex justify-between gap-3"><dt class="text-muted">{t('tn.f.status')}</dt><dd>{t(`tn.status.${v.status}`)}</dd></div>
	<div class="flex justify-between gap-3"><dt class="text-muted">{t('tn.f.home_team')}</dt><dd class="text-right">{home || t('tn.f.home_team_none')}</dd></div>
	<div class="flex justify-between gap-3 border-t border-line pt-1"><dt class="text-muted">{t('tn.f.registration_open')}</dt><dd>{v.registration_open ? t('common.yes') : t('common.no')}</dd></div>
	<div class="flex justify-between gap-3"><dt class="text-muted">{t('tn.f.max_teams')}</dt><dd class="font-mono">{v.max_teams || dash}</dd></div>
	<div class="flex justify-between gap-3 border-t border-line pt-1"><dt class="text-muted">{t('tn.f.entry_fee')}</dt><dd class="font-mono">{v.entry_fee_amount || dash}</dd></div>
	<div class="flex justify-between gap-3"><dt class="text-muted">{t('tn.f.deposit')}</dt><dd class="font-mono">{v.deposit_amount || dash}</dd></div>
	<div class="flex justify-between gap-3"><dt class="text-muted">{t('tn.f.lunch_mode')}</dt><dd>{t(`tn.lunch.${v.lunch_mode}`)}</dd></div>
</dl>
