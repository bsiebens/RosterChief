<script lang="ts">
	import { Plus } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { blankPlayer, type PlayerDraft } from '#lib/tournaments-public.ts';

	/** "Add player" and "Add staff member", for the top right of the box that holds the PlayersEditor list. */
	let { players = $bindable(), dates, mandatory = false }: { players: PlayerDraft[]; dates: string[]; mandatory?: boolean } = $props();
	const add = (role: 'player' | 'staff') => (players = [...players, blankPlayer(role, mandatory ? [...dates] : [])]);
</script>

<div class="flex flex-wrap justify-end gap-2">
	<button type="button" class="btn btn-outline btn-sm gap-1.5" onclick={() => add('player')}><Plus size={15} aria-hidden="true" /> {t('tp.add_player')}</button>
	<button type="button" class="btn btn-outline btn-sm gap-1.5" onclick={() => add('staff')}><Plus size={15} aria-hidden="true" /> {t('tp.add_staff')}</button>
</div>
