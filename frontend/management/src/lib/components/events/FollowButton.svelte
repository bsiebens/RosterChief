<script lang="ts">
	import { Bell, BellRing, LoaderCircle } from '@lucide/svelte';
	import { onMount } from 'svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { api } from '#lib/management-api.ts';

	/**
	 * Follow a game (or all of a team's games) to get its live pushes: goals, penalties, period ends and the result.
	 * Opt-in per account; the push still follows your own notification settings.
	 */
	let { kind, id, class: cls = 'btn btn-outline gap-2' }: { kind: 'event' | 'team'; id: string; class?: string } = $props();

	let following = $state(false);
	let viaTeam = $state(false);
	let busy = $state(true);

	const path = $derived(kind === 'event' ? `/events/${id}/follow` : `/events/follow-teams/${id}`);

	onMount(async () => {
		const res = kind === 'event' ? await api<{ directly: boolean; via_team: boolean }>('GET', path) : await api<{ team_ids: string[] }>('GET', '/events/follow-teams');
		if (res.ok && res.data) {
			if ('team_ids' in res.data) following = res.data.team_ids.includes(id);
			else ((following = res.data.directly), (viaTeam = res.data.via_team));
		}
		busy = false;
	});

	async function toggle() {
		busy = true;
		const res = await api<{ directly?: boolean; via_team?: boolean; team_ids?: string[] }>(following ? 'DELETE' : 'PUT', path);
		if (res.ok && res.data) {
			if (res.data.team_ids) following = res.data.team_ids.includes(id);
			else ((following = !!res.data.directly), (viaTeam = !!res.data.via_team));
		}
		busy = false;
	}
	const on = $derived(following || viaTeam);
</script>

<button class={cls} type="button" disabled={busy || (viaTeam && !following)} aria-pressed={on} onclick={toggle} title={viaTeam && !following ? t('follow.via_team') : t('follow.hint')}>
	{#if busy}<LoaderCircle size={16} class="animate-spin" />{:else if on}<BellRing size={16} />{:else}<Bell size={16} />{/if}
	{on ? t('follow.following') : kind === 'team' ? t('follow.team') : t('follow.game')}
</button>
