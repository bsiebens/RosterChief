<script lang="ts">
	import { UsersRound } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import ClubCrest from './ClubCrest.svelte';

	/**
	 * The "who is this about" switcher of the member and coach apps: Everyone, then each person of the family. The selected chip takes
	 * the club accent colour with a white avatar; the others carry the club's dark colour on their avatar. `selected` is a member id, '' for everyone.
	 */
	type Person = { id: string; name: string; first_name?: string; photo_url?: string | null };
	let { people, selected = $bindable(''), viewer = '', compact = false, all = true }: { people: Person[]; selected: string; viewer?: string; compact?: boolean; all?: boolean } = $props();
</script>

{#if people.length > 1}
	<div class="flex max-w-full items-center gap-2 overflow-x-auto {compact ? '' : 'pb-1'}" role="group" aria-label={t('portal.person')}>
		{#if all}<button class="portal-person" class:compact type="button" aria-pressed={!selected} onclick={() => (selected = '')}>
			<span class="grid {compact ? 'size-6' : 'size-7'} place-items-center rounded-full {!selected ? 'bg-white text-club' : 'bg-primary text-primary-content'}"><UsersRound size={compact ? 12 : 14} aria-hidden="true" /></span>{t('portal.all')}
		</button>{/if}
		{#each people as person (person.id)}
			{@const on = selected === person.id}
			<button class="portal-person" class:compact type="button" aria-pressed={on} onclick={() => (selected = person.id)}>
				<ClubCrest name={person.name} logoUrl={person.photo_url} size={compact ? 24 : 27} bg={on ? '#fff' : 'var(--color-primary)'} fg={on ? 'var(--color-club)' : 'var(--color-primary-content)'} />{person.id === viewer ? t('portal.me') : person.first_name || person.name}
			</button>
		{/each}
	</div>
{/if}
