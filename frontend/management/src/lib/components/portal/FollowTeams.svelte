<script lang="ts">
	import { Radio, Shirt, X } from '@lucide/svelte';
	import Alert from '#lib/components/Alert.svelte';
	import { formatDateTime } from '#lib/events/types.ts';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';
	import SaveStatus from './SaveStatus.svelte';

	type State = { teams: { id: string; name: string; mine: boolean; followed: boolean }[]; games: { event_id: string; title: string; start: string }[] };
	let { initial }: { initial: State } = $props();

	// svelte-ignore state_referenced_locally
	let follows = $state<State>(initial);
	let status = $state<'idle' | 'saving' | 'saved' | 'error'>('idle');
	let timer: ReturnType<typeof setTimeout> | undefined;

	/** Following saves the moment a switch is flipped, like the notification settings. */
	async function apply(method: 'PUT' | 'DELETE', path: string) {
		clearTimeout(timer);
		status = 'saving';
		const result = await api<State>(method, path);
		if (result.ok && result.data) {
			follows = result.data;
			status = 'saved';
			timer = setTimeout(() => (status = 'idle'), 2500);
		} else {
			status = 'error';
		}
	}
	const toggleTeam = (id: string, on: boolean) => apply(on ? 'PUT' : 'DELETE', `/portal/me/follows/teams/${id}`);
</script>

<SaveStatus {status} />
<section id="follow" class="card scroll-mt-4" aria-labelledby="follow-title">
	<div class="card-h"><Radio size={17} aria-hidden="true" /><h2 id="follow-title">{t('portal.follow_title')}</h2></div>
	<p class="px-5 pt-4 text-sm text-muted">{t('portal.follow_teams_help')}</p>
	{#if status === 'error'}<div class="p-5 pb-0"><Alert kind="error">{t('portal.save_failed')}</Alert></div>{/if}
	<ul class="grid px-5 pb-2 sm:grid-cols-2 sm:gap-x-10">
		{#each follows.teams as team (team.id)}
			<li class="flex min-h-12 items-center gap-3 border-b border-rule py-2 last:border-b-0 sm:[&:nth-last-child(2):nth-child(odd)]:border-b-0">
				<Shirt size={16} class="shrink-0 text-muted" aria-hidden="true" />
				<span class="min-w-0 flex-1 font-semibold text-ink">{team.name}{#if team.mine}<span class="badge badge-sm badge-outline ml-2">{t('portal.follow_mine')}</span>{/if}</span>
				<input type="checkbox" class="toggle" aria-label={team.name} checked={team.followed} onchange={(e) => toggleTeam(team.id, e.currentTarget.checked)} />
			</li>
		{:else}
			<li class="py-3 text-sm text-muted sm:col-span-2">{t('portal.follow_none')}</li>
		{/each}
	</ul>
	{#if follows.games.length}
		<p class="border-t border-line px-5 pt-3 pb-1 font-mono text-[11px] tracking-[.14em] text-muted uppercase">{t('portal.follow_games')}</p>
		<ul class="divide-y divide-rule">
			{#each follows.games as game (game.event_id)}
				<li class="flex min-h-12 items-center gap-3 px-5 py-2"><span class="min-w-0 flex-1"><b class="block text-ink">{game.title}</b><span class="text-xs text-muted">{formatDateTime(game.start)}</span></span><button type="button" class="btn btn-ghost btn-square btn-sm" aria-label={t('portal.unfollow_game')} onclick={() => apply('DELETE', `/portal/me/follows/games/${game.event_id}`)}><X size={16} aria-hidden="true" /></button></li>
			{/each}
		</ul>
	{/if}
</section>
