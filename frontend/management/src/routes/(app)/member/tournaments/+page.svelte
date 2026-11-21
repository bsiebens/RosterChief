<script lang="ts">
	import { Clock, HandHeart, MapPin, Users } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { usePageHeader } from '#lib/header.svelte.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import { pageTitle } from '#lib/title.svelte.ts';
	import { dayLabel, range, timeLabel, type PortalShift } from '#lib/tournaments-public.ts';

	let { data } = $props();
	usePageHeader(() => ({ title: t('tv.title') }));

	// Shifts changed here replace the loaded ones until the page loads again.
	let changed = $state<Record<string, PortalShift>>({});
	let busy = $state('');
	let errors = $state<Record<string, string>>({});
	const shift = (s: PortalShift) => changed[s.id] ?? s;

	/** A tournament's shifts grouped by the day they start. */
	function byDay(shifts: PortalShift[]) {
		const days = new Map<string, PortalShift[]>();
		for (const s of shifts) {
			const day = s.starts_at.slice(0, 10);
			days.set(day, [...(days.get(day) ?? []), s]);
		}
		return [...days.entries()].sort(([a], [b]) => a.localeCompare(b));
	}

	async function toggle(s: PortalShift) {
		if (busy) return;
		busy = s.id;
		errors[s.id] = '';
		const res = await api<PortalShift>(s.claimed_by_me ? 'DELETE' : 'POST', `/portal/tournaments/tasks/${encodeURIComponent(s.id)}/claim`);
		busy = '';
		if (res.ok && res.data) changed[s.id] = res.data;
		else errors[s.id] = res.code === 'cannot_claim' ? t('tv.err.cannot_claim') : res.status === 0 ? t('reg.err.network') : res.message || t('reg.err.generic');
	}
</script>

<svelte:head><title>{pageTitle(t('tv.title'))}</title></svelte:head>

<div class="grid min-w-0 gap-5">
	{#if !data.tournaments.length}
		<div class="card card-body items-center gap-2 py-10 text-center" role="status">
			<HandHeart size={28} class="text-dim" aria-hidden="true" />
			<p class="font-display text-lg font-bold text-ink">{t('tv.empty')}</p>
		</div>
	{/if}

	{#each data.tournaments as tn (tn.slug)}
		<section class="grid gap-3" aria-labelledby="tv-{tn.slug}">
			<div>
				<h2 id="tv-{tn.slug}" class="font-display text-xl font-extrabold text-ink uppercase">{tn.name}</h2>
				<p class="text-sm text-muted">{range(tn.start_date, tn.end_date)} · <a class="link" href="/tournaments/{tn.slug}">{t('tv.public_page')}</a></p>
			</div>
			{#each byDay(tn.shifts) as [day, shifts] (day)}
				<div class="grid gap-2">
					<h3 class="font-display text-xs font-bold tracking-[.12em] text-muted uppercase">{dayLabel(day)}</h3>
					<ul class="card divide-y divide-rule overflow-hidden">
						{#each shifts as raw (raw.id)}
							{@const s = shift(raw)}
							<li class="grid gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
								<div class="min-w-0">
									<b class="block text-ink">{s.title}</b>
									<span class="mt-0.5 flex flex-wrap items-center gap-x-3 text-sm text-muted">
										<span class="inline-flex items-center gap-1"><Clock size={13} aria-hidden="true" /> {timeLabel(s.starts_at)} – {timeLabel(s.ends_at)}</span>
										{#if s.location_label}<span class="inline-flex items-center gap-1"><MapPin size={13} aria-hidden="true" /> {s.location_label}</span>{/if}
										<span class="inline-flex items-center gap-1"><Users size={13} aria-hidden="true" /> {t('tv.filled', { claimed: s.claimed, needed: s.needed })}</span>
									</span>
									{#if s.description}<p class="mt-1 text-sm text-muted">{s.description}</p>{/if}
									{#if s.claimed_by.length}<p class="mt-1 text-xs text-muted">{s.claimed_by.join(', ')}</p>{/if}
									{#if errors[s.id]}<p class="mt-1 text-xs text-error" role="alert">{errors[s.id]}</p>{/if}
								</div>
								<div class="flex items-center gap-2">
									{#if s.claimed_by_me}
										<span class="badge badge-success">{t('tv.mine')}</span>
										<button type="button" class="btn btn-outline btn-sm" disabled={busy === s.id} onclick={() => toggle(s)}>{t('tv.unclaim')}</button>
									{:else if s.is_full}
										<span class="badge badge-ghost">{t('tv.full')}</span>
									{:else}
										<button type="button" class="btn btn-primary btn-sm" disabled={busy === s.id} onclick={() => toggle(s)}>{t('tv.claim')}</button>
									{/if}
								</div>
							</li>
						{/each}
					</ul>
				</div>
			{/each}
			{#if !tn.shifts.length}<Alert kind="info">{t('tv.none_open')}</Alert>{/if}
		</section>
	{/each}
</div>
