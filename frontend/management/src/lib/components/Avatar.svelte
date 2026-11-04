<script lang="ts">
	/** Round avatar: the photo when there is one, else initials on the club's secondary colour (old panel's `_avatar.html`). */
	let { first_name = '', last_name = '', photoUrl = null, size = 'h-8 w-8', text = 'text-xs' }: { first_name?: string; last_name?: string; photoUrl?: string | null; size?: string; text?: string } = $props();
	const initials = $derived(((first_name[0] ?? '') + (last_name[0] ?? '')).toUpperCase() || '?');
</script>

<span class="inline-flex shrink-0 no-underline">
	{#if photoUrl}
		<img src={photoUrl} alt="" class="{size} rounded-full object-cover" />
	{:else}
		<span class="{size} {text} flex items-center justify-center rounded-full bg-club font-display font-extrabold text-club-content">{initials}</span>
	{/if}
</span>
