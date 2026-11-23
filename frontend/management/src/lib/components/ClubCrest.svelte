<script lang="ts">
	/**
	 * Club crest: the club's logo on a white circle, else the initials of the first two words of its name on the
	 * accent colour. `bg`/`fg` override the accent (the club picker shows each club in its own colours).
	 */
	let { name, logoUrl = null, size = 30, bg, fg }: { name: string; logoUrl?: string | null; size?: number; bg?: string; fg?: string } = $props();

	const initials = $derived(
		name
			.split(/\s+/)
			.filter(Boolean)
			.slice(0, 2)
			.map((w) => w[0])
			.join('')
			.toUpperCase() || '?'
	);
	let failed = $state(false); // a logo that does not load falls back to the initials
	$effect(() => {
		logoUrl;
		failed = false;
	});
	const dim = $derived(`width:${size}px;height:${size}px`);
</script>

{#if logoUrl && !failed}
	<img class="crest shrink-0 object-cover" style={dim} src={logoUrl} alt="" width={size} height={size} onerror={() => (failed = true)} />
{:else}
	<span class="crest flex shrink-0 items-center justify-center bg-club" style="{dim};{bg ? `background:${bg};` : ''}">
		<span class="font-display leading-none font-extrabold text-club-content" style="font-size:{Math.round(size / 3)}px;{fg ? `color:${fg};` : ''}">{initials}</span>
	</span>
{/if}
