<script lang="ts">
	/** A player's picture, else their initials on a dark circle (unmatched game-sheet names only have initials). */
	let { photoUrl = null, initials = '?', size = 'size-8' }: { photoUrl?: string | null; initials?: string; size?: string } = $props();
</script>

{#if photoUrl}
	<img src={photoUrl} alt="" class="{size} shrink-0 rounded-full object-cover" />
{:else}
	<span class="{size} flex shrink-0 items-center justify-center rounded-full bg-ink font-display text-[10px] font-extrabold text-white" aria-hidden="true">{initials || '?'}</span>
{/if}
