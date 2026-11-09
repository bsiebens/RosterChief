<script lang="ts">
	import type { Component, Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';
	import { ChevronRight } from '@lucide/svelte';

	/**
	 * One row of an `ActionList`: an icon and a label, as a link (`href`) or a button (any button attribute, `type` defaults to "button").
	 * `danger` makes it red (delete, cancel), `chevron` marks a row that opens another page.
	 */
	type Props = { icon: Component<{ size?: number; class?: string; 'aria-hidden'?: boolean | 'true' | 'false' }>; href?: string; danger?: boolean; chevron?: boolean; children: Snippet } & HTMLButtonAttributes;
	let { icon: Icon, href, danger = false, chevron = false, children, type = 'button', ...rest }: Props = $props();
	const cls = $derived(`flex w-full items-center gap-3 px-3.5 py-2.5 text-left text-sm font-semibold transition-colors hover:bg-surface disabled:opacity-50 ${danger ? 'text-danger' : 'text-ink'}`);
</script>

{#snippet inner()}
	<Icon size={16} class={danger ? '' : 'text-muted'} aria-hidden="true" />
	{@render children()}
	{#if chevron}<ChevronRight size={14} class="ml-auto text-dim" aria-hidden="true" />{/if}
{/snippet}

{#if href}
	<a {href} class={cls}>{@render inner()}</a>
{:else}
	<button {type} class={cls} {...rest}>{@render inner()}</button>
{/if}
