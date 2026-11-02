<script lang="ts">
	import { Building2, Receipt, RefreshCw, Users } from '@lucide/svelte';
	import { ALERTS, money, num, type Dashboard } from '#lib/dashboard.ts';
	import Alert from '#lib/components/Alert.svelte';
	import BarChart from '#lib/components/BarChart.svelte';
	import JobLog from '#lib/components/JobLog.svelte';
	import Kpi from '#lib/components/Kpi.svelte';

	let { data } = $props();

	// The streamed snapshot is the starting point; "Refresh" swaps in a freshly computed one.
	let fresh = $state<Dashboard | null>(null);
	let refreshing = $state(false);
	let refreshError = $state('');

	async function refresh() {
		refreshing = true;
		refreshError = '';
		try {
			const res = await fetch('/api/platform/v1/stats/dashboard?refresh=true');
			if (res.ok) fresh = await res.json();
			else refreshError = `Refresh failed (${res.status}).`;
		} catch {
			refreshError = 'Refresh failed. Check your connection.';
		} finally {
			refreshing = false;
		}
	}

	const FUNNEL_ICONS: Record<string, typeof Users> = { clubs: Building2, members: Users, billed: Receipt };
	const at = (iso: string) => new Date(iso).toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit' });
	const val = (d: Dashboard, group: 'totals' | 'attention', key: string) => num(d[group][key]?.value);
	const alerts = (d: Dashboard) => Object.entries(d.attention).filter(([key, m]) => key in ALERTS && num(m.value) > 0);
	const redFirst = <T extends [string, unknown]>(a: T[]) => [...a].sort((x, y) => Number(ALERTS[y[0]].tone === 'club') - Number(ALERTS[x[0]].tone === 'club'));
</script>

<svelte:head><title>Platform health · RosterChief</title></svelte:head>

{#await data.dashboard}
	<div class="flex items-end justify-between">
		<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">Platform health</h1>
	</div>
	<div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6" aria-busy="true" aria-label="Loading statistics">
		{#each { length: 6 } as _, i (i)}<div class="card h-[104px] animate-pulse bg-rule"></div>{/each}
	</div>
	<div class="card h-72 animate-pulse bg-rule"></div>
{:then result}
	{@const d = fresh ?? ('data' in result ? result.data : null)}
	{#if !d}
		<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">Platform health</h1>
		<Alert kind="error" title="Dashboard unavailable">{'error' in result ? result.error : 'No data.'} Reload the page to try again.</Alert>
	{:else}
		<div class="flex flex-wrap items-end justify-between gap-3">
			<div>
				<h1 class="font-display text-2xl leading-none font-extrabold tracking-[.04em] text-ink uppercase">Platform health</h1>
				<p class="mt-1.5 font-mono text-[11px] text-muted">Updated {at(d.generated_at)} · cached for two minutes</p>
			</div>
			<button class="btn btn-outline btn-sm" onclick={refresh} disabled={refreshing}>
				<RefreshCw size={14} class={refreshing ? 'animate-spin' : ''} aria-hidden="true" /> Refresh
			</button>
		</div>
		{#if refreshError}<Alert kind="error">{refreshError}</Alert>{/if}

		<div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
			<Kpi label="clubs live" value={val(d, 'totals', 'clubs_active')} sub="{val(d, 'totals', 'clubs_archived')} archived" />
			<Kpi label="members" value={val(d, 'totals', 'members_total')} sub="{val(d, 'totals', 'club_admins')} club admins" />
			<Kpi
				label="dues owed"
				value={money(d.attention.dues_owed?.value)}
				sub="{val(d, 'attention', 'dues_in_grace')} in grace · {val(d, 'attention', 'dues_overdue')} overdue"
				tone={val(d, 'attention', 'dues_overdue') ? 'club' : 'none'}
			/>
			<Kpi label="near member limit" value={val(d, 'attention', 'clubs_near_member_limit')} sub="clubs at 90%+" tone={val(d, 'attention', 'clubs_near_member_limit') ? 'warn' : 'none'} />
			<Kpi label="failed jobs" value={val(d, 'attention', 'failed_jobs_24h')} sub="last 24h" tone={val(d, 'attention', 'failed_jobs_24h') ? 'club' : 'none'} />
			<Kpi label="admins w/o mfa" value={val(d, 'attention', 'admins_pending_mfa')} sub="staff and club admins" tone={val(d, 'attention', 'admins_pending_mfa') ? 'warn' : 'none'} />
		</div>

		<div class="grid grid-cols-1 gap-4 xl:grid-cols-[1.6fr_1fr]">
			<div class="flex flex-col gap-4">
				<div class="grid gap-4 md:grid-cols-2">
					<div class="card p-4">
						<div class="mb-4 font-display text-sm font-extrabold tracking-[.1em] text-ink uppercase">New clubs · 12 months</div>
						<BarChart series={d.charts.new_clubs} label="New clubs" />
					</div>
					<div class="card p-4">
						<div class="mb-4 font-display text-sm font-extrabold tracking-[.1em] text-ink uppercase">Payments received · 12 months</div>
						<BarChart series={d.charts.payments_received_eur} label="Payments received" color="#14B8E8" money />
					</div>
				</div>

				<div class="card flex flex-col">
					<div class="flex items-center border-b border-line px-4 py-3">
						<span class="font-display text-sm font-extrabold tracking-[.1em] text-ink uppercase">Club health</span>
						<span class="flex-1"></span>
						<span class="font-mono text-[11px] text-muted">sorted by risk</span>
					</div>
					<div class="overflow-x-auto">
						<table class="table">
							<thead>
								<tr><th>Club</th><th>Plan</th><th class="text-right">Members</th><th class="text-right">Dues owed</th><th>Risk</th></tr>
							</thead>
							<tbody>
								{#each d.clubs as club (club.club_id)}
									<tr>
										<td>
											<a class="font-semibold text-ink hover:underline" href="/clubs/{club.club_id}">{club.name}</a>
											<div class="font-mono text-[11px] text-muted">{club.slug}</div>
										</td>
										<td class="font-mono text-xs">{club.plan ?? '—'}</td>
										<td class="text-right font-mono tabular-nums">
											{club.members ?? '—'}{#if club.member_limit}<span class="text-muted"> / {club.member_limit}</span>{/if}
										</td>
										<td class="text-right font-mono tabular-nums {num(club.dues_owed) ? 'text-club-dark' : 'text-muted'}">{num(club.dues_owed) ? money(club.dues_owed) : '—'}</td>
										<td>
											<span class="badge {club.risk === 'high' ? 'badge-error' : club.risk === 'watch' ? 'badge-warning' : 'badge-success'}" title={club.reason}>{club.risk}</span>
											{#if club.risk !== 'ok'}<span class="ml-2 text-xs text-muted">{club.reason}</span>{/if}
										</td>
									</tr>
								{:else}
									<tr><td colspan="5" class="text-center text-muted">No clubs yet.</td></tr>
								{/each}
							</tbody>
						</table>
					</div>
				</div>

				<div class="card p-4">
					<div class="mb-3 font-display text-sm font-extrabold tracking-[.1em] text-ink uppercase">Onboarding</div>
					<div class="grid gap-4 sm:grid-cols-3">
						{#each d.funnel as step (step.key)}
							{@const StepIcon = FUNNEL_ICONS[step.key]}
							<div>
								<div class="mb-1 flex items-center gap-1.5 font-mono text-[11px] text-muted">
									{#if StepIcon}<StepIcon size={12} aria-hidden="true" />{/if}
									{step.step}
								</div>
								<div class="font-display text-2xl leading-none font-extrabold text-ink tabular-nums">{step.count}</div>
								<div class="mt-1.5 h-1.5 bg-rule"><div class="h-full {step.count === d.funnel[0].count ? 'bg-ok' : step.count === 0 ? 'bg-club' : 'bg-warn'}" style="width: {d.funnel[0].count ? (step.count / d.funnel[0].count) * 100 : 0}%"></div></div>
							</div>
						{/each}
					</div>
				</div>
			</div>

			<div class="flex flex-col gap-4">
				<div class="rounded bg-ink p-4 text-white">
					<div class="mb-3 font-display text-sm font-extrabold tracking-[.1em] uppercase">Alerts</div>
					<div class="flex flex-col gap-2.5 font-mono text-xs">
						{#each redFirst(alerts(d)) as [key, m] (key)}
							<div class="border-l-2 pl-2.5 {ALERTS[key].tone === 'club' ? 'border-club' : 'border-warn'}">
								<div>{num(m.value)} · {m.label}</div>
								<div class="text-on-dark-dim">{ALERTS[key].hint}</div>
							</div>
						{:else}
							<div class="text-on-dark-dim">Nothing needs attention.</div>
						{/each}
					</div>
				</div>

				<div class="card p-4">
					<div class="mb-3 font-display text-sm font-extrabold tracking-[.1em] text-ink uppercase">Plans</div>
					<div class="flex flex-col gap-2.5 font-mono text-[11px] text-slate">
						{#each d.plans as p (p.plan)}
							<div>
								<div class="mb-1 flex justify-between"><span>{p.plan}</span><span>{p.clubs}</span></div>
								<div class="h-1.5 bg-rule"><div class="h-full bg-ink" style="width: {(p.clubs / Math.max(val(d, 'totals', 'clubs_active'), 1)) * 100}%"></div></div>
							</div>
						{:else}
							<div class="text-muted">No clubs yet.</div>
						{/each}
					</div>
				</div>

				<div class="card p-4">
					<div class="mb-3 font-display text-sm font-extrabold tracking-[.1em] text-ink uppercase">Feature adoption</div>
					<div class="flex flex-col gap-2.5 font-mono text-[11px] text-slate">
						{#each d.features as f (f.code)}
							<div>
								<div class="mb-1 flex justify-between">
									<span>{f.name}{#if f.overridden}<span class="text-muted"> · {f.overridden} override{f.overridden === 1 ? '' : 's'}</span>{/if}</span>
									<span>{f.clubs}/{val(d, 'totals', 'clubs_active')}</span>
								</div>
								<div class="h-1.5 bg-rule"><div class="h-full bg-ink" style="width: {(f.clubs / Math.max(val(d, 'totals', 'clubs_active'), 1)) * 100}%"></div></div>
							</div>
						{:else}
							<div class="text-muted">No feature flags yet.</div>
						{/each}
					</div>
				</div>
			</div>
		</div>
	{/if}
{/await}

<JobLog />
