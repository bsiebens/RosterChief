<script lang="ts">
	import { Layers } from '@lucide/svelte';
	import InfoTip from '#lib/components/InfoTip.svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/** Read-only on the profile: groups are set up and populated on the Groups page. */
	let { groups }: { groups: { id: string; name: string }[] } = $props();
</script>

<div class="card card-body">
	<h2 class="card-title"><Layers size={18} /> {t('members.detail.groups')} <InfoTip label={t('families.info_label')} text={t('groups.profile_note')} /></h2>
	{#if groups.length}
		<div class="flex flex-wrap gap-2">
			{#each groups as g (g.id)}<a class="badge badge-outline hover:bg-surface" href="/groups/{g.id}">{g.name}</a>{/each}
		</div>
	{:else}
		<p class="text-sm text-muted">{t('members.detail.no_groups')}</p>
	{/if}
</div>
