<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { ArrowLeft, Check, RotateCcw, Users, X } from '@lucide/svelte';
	import { formatDateTime, statusLabelKey, statusStyle, type AttendanceRow } from '#lib/events/types.ts';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { pageTitle } from '#lib/title.svelte.ts';

	let { data } = $props();
	let query = $state('');
	let busy = $state('');
	let message = $state('');
	const rows = $derived(data.attendance.rows.filter((row) => !query.trim() || row.member.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())));
	usePageHeader(() => ({ title: t('events.tab.attendance'), context: data.event.title }));

	async function checkIn(row: AttendanceRow, showedUp: boolean | null) {
		if (busy) return;
		busy = row.member.id;
		message = '';
		const response = await api('PUT', `/events/${data.event.id}/attendance/${row.member.id}/check-in`, { body: { showed_up: showedUp } });
		if (response.ok) await invalidateAll();
		else message = response.message ?? t('common.error.generic');
		busy = '';
	}
</script>

<svelte:head><title>{pageTitle(t('events.tab.attendance'))}</title></svelte:head>

<div class="grid w-full max-w-5xl min-w-0 gap-4">
	<a class="btn btn-outline min-h-11 justify-self-start gap-2" href="/coach"><ArrowLeft size={16} aria-hidden="true" />{t('portal.back_today')}</a>
	<section class="card card-body gap-3">
		<h2 class="card-title"><Users size={16} aria-hidden="true" />{data.event.title}</h2>
		<p class="text-sm text-muted" aria-live="polite">{formatDateTime(data.event.start)} · {t('events.att.checked_in')} {data.attendance.checked_in}/{data.attendance.rows.length}</p>
		<label class="grid max-w-sm gap-1 text-sm font-semibold">
			{t('portal.search_players')}
			<input type="search" class="input input-bordered w-full" bind:value={query} />
		</label>
		{#if message}<p class="text-sm text-error" role="alert">{message}</p>{/if}
		<ul class="divide-y divide-rule">
			{#each rows as row (row.member.id)}
				<li class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-3">
					<div class="flex min-w-0 flex-1 basis-32 items-center gap-2">
						{#if row.jersey_number != null}<span class="rounded-box bg-subhead px-2 py-1 font-mono text-xs text-muted">#{row.jersey_number}</span>{/if}
						<div class="min-w-0">
						<p class="font-semibold">{row.member.name}</p>
						<div class="mt-1 flex flex-wrap items-center gap-1.5">
							<span class="badge badge-sm border {statusStyle(row.status)}">{t(statusLabelKey(row.status))}</span>
							{#if row.showed_up !== null}<span class="text-xs text-muted">{t(row.showed_up ? 'events.att.checked_in' : 'events.att.no_show')}</span>{/if}
						</div>
						</div>
					</div>
					<div class="flex flex-wrap gap-1.5">
						<button type="button" class="btn btn-sm min-h-11 {row.showed_up === true ? 'btn-primary' : 'btn-outline'}" disabled={!!busy} aria-label={t('portal.check_in') + ' · ' + row.member.name} aria-pressed={row.showed_up === true} onclick={() => checkIn(row, true)}><Check size={16} aria-hidden="true" />{t(row.showed_up === true ? 'events.att.checked_in' : 'portal.check_in')}</button>
						<button type="button" class="btn btn-square size-11 {row.showed_up === false ? 'btn-error' : 'btn-outline'}" disabled={!!busy} aria-label={t('events.att.no_show') + ' · ' + row.member.name} aria-pressed={row.showed_up === false} onclick={() => checkIn(row, false)}><X size={18} aria-hidden="true" /></button>
						<button type="button" class="btn btn-square btn-outline size-11" disabled={!!busy || row.showed_up === null} aria-label={t('portal.clear_check_in') + ' · ' + row.member.name} onclick={() => checkIn(row, null)}><RotateCcw size={16} aria-hidden="true" /></button>
					</div>
				</li>
			{:else}
				<li class="text-sm text-muted">{t('portal.no_players')}</li>
			{/each}
		</ul>
	</section>
</div>
