<script lang="ts">
	import { Camera, Image as ImageIcon } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { MemberDetail } from '#lib/members/detail-types.ts';

	let { member }: { member: MemberDetail } = $props();
	const slots = $derived([
		{ key: 'profile', label: t('members.detail.pictures.profile'), url: member.profile_photo_url ?? null },
		{ key: 'player', label: t('members.detail.pictures.player'), url: member.player_photo_url ?? null }
	]);
</script>

<div class="card card-body">
	<h2 class="card-title"><Camera size={18} /> {t('members.detail.pictures')}</h2>
	<div class="grid grid-cols-2 gap-4">
		{#each slots as slot (slot.key)}
			<figure class="flex flex-col items-center gap-2">
				{#if slot.url}
					<img src={slot.url} alt={slot.label} class="aspect-square w-full max-w-48 rounded-lg object-cover" />
				{:else}
					<div class="flex aspect-square w-full max-w-48 flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-rule text-muted">
						<ImageIcon size={28} />
						<span class="text-xs">{t('members.detail.pictures.none')}</span>
					</div>
				{/if}
				<figcaption class="text-sm font-semibold text-ink">{slot.label}</figcaption>
			</figure>
		{/each}
	</div>
	{#if !member.photo_public_consent}<p class="text-xs text-muted">{t('members.detail.pictures.not_public')}</p>{/if}
</div>
