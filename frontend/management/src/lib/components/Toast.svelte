<script lang="ts">
	import { Check, CircleAlert } from '@lucide/svelte';

	/**
	 * A short confirmation that stands out and goes away by itself: a solid bar above the bottom action band on a phone, a card in the corner
	 * on larger screens. Errors stay a little longer. `onhide` is called when it times out, so the owner can clear the message.
	 */
	let { message, error = false, onhide, seconds = 4, aboveBand = false }: { message: string; error?: boolean; onhide: () => void; seconds?: number; /** The page has an action band above the tab bar: sit above that too. */ aboveBand?: boolean } = $props();
	$effect(() => {
		if (!message) return;
		const timer = setTimeout(onhide, (error ? seconds + 3 : seconds) * 1000);
		return () => clearTimeout(timer);
	});
</script>

{#if message}
	<div class="pointer-events-none fixed inset-x-4 z-40 flex {aboveBand ? 'bottom-[calc(3.5rem+4.75rem+env(safe-area-inset-bottom)+0.5rem)]' : 'bottom-[calc(3.5rem+env(safe-area-inset-bottom)+0.75rem)]'} justify-center md:inset-x-auto md:right-6 md:bottom-6" role={error ? 'alert' : 'status'}>
		<div class="toast-in pointer-events-auto flex w-full max-w-md items-center gap-3 rounded-box px-4 py-3 text-[15px] font-semibold shadow-xl {error ? 'bg-danger text-[#fff]' : 'bg-ok text-[#fff]'}">
			{#if error}<CircleAlert size={20} class="shrink-0" aria-hidden="true" />{:else}<Check size={20} class="shrink-0" aria-hidden="true" />{/if}
			<span class="min-w-0 flex-1">{message}</span>
			<button type="button" class="shrink-0 rounded px-2 py-1 text-sm underline" onclick={onhide}>OK</button>
		</div>
	</div>
{/if}
