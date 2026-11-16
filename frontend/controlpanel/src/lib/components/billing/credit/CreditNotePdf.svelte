<script lang="ts">
	// A credit note number that opens its PDF. The PDF is fetched first so a failure (for example no PDF renderer on the
	// server) is shown inline instead of in a blank tab.
	let { id, number }: { id: string; number: string } = $props();

	let busy = $state(false);
	let error = $state('');

	async function open() {
		busy = true;
		error = '';
		try {
			const res = await fetch(`/api/platform/v1/billing/credit-notes/${id}/pdf`);
			if (!res.ok) {
				const json = await res.json().catch(() => null);
				error = json?.error?.message ?? 'The PDF could not be created.';
				return;
			}
			const url = URL.createObjectURL(await res.blob());
			if (!window.open(url, '_blank')) {
				const a = document.createElement('a');
				a.href = url;
				a.download = `${number}.pdf`;
				a.click();
			}
			setTimeout(() => URL.revokeObjectURL(url), 60_000);
		} catch {
			error = 'Could not reach the server. Check your connection and try again.';
		} finally {
			busy = false;
		}
	}
</script>

<button type="button" class="link font-mono font-semibold" onclick={open} disabled={busy} title="Open the credit note PDF">{busy ? 'Preparing…' : number}</button>
{#if error}<span class="block max-w-60 text-[11px] whitespace-normal text-club-dark" role="alert">{error}</span>{/if}
