<script lang="ts">
	/** A team's crest: its logo when it has one, else the initials on the team colour (or the club colour when it has none). */
	let { name, logoUrl = null, color = '', size = 'size-8', text = 'text-xs' }: { name: string; logoUrl?: string | null; color?: string; size?: string; text?: string } = $props();
	let failed = $state(false);
	$effect(() => {
		logoUrl;
		failed = false;
	});
	const initials = $derived(
		name
			.split(/\s+/)
			.filter(Boolean)
			.slice(0, 2)
			.map((w) => w[0])
			.join('')
			.toUpperCase() || '?'
	);
</script>

{#if logoUrl && !failed}
	<img src={logoUrl} alt="" class="{size} shrink-0 rounded-md border border-line bg-white object-contain" onerror={() => (failed = true)} />
{:else}
	<span class="{size} {text} flex shrink-0 items-center justify-center rounded-md font-display font-extrabold {color ? 'text-white' : 'bg-club text-club-content'}" style:background={color || undefined} aria-hidden="true">{initials}</span>
{/if}
