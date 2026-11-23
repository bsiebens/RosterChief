<script lang="ts">
	import { ArrowLeft, Check, Eye, ShieldCheck, Trash2 } from '@lucide/svelte';
	import { onMount } from 'svelte';
	import { chatApi, errorText, type Message, type MessagesPage, type Report } from '#lib/chat.ts';
	import { getLang, t } from '#lib/i18n.svelte.ts';
	import MessageList from './MessageList.svelte';

	/** For people who may moderate: the reported messages, a read-only look at the conversation around one, delete, and "handled". */
	let { heightClass = 'h-[calc(100dvh-8rem)]', openCount = $bindable(0) }: { heightClass?: string; /** The number of reports not yet handled, for the badge on the tab. */ openCount?: number } = $props();

	let reports = $state<Report[] | null>(null);
	let error = $state('');
	let showHandled = $state(false);
	let busy = $state('');
	let viewing = $state<Report | null>(null);
	let page = $state<MessagesPage | null>(null);
	let viewError = $state('');

	async function load() {
		const res = await chatApi.reports();
		if (res.ok && res.data) {
			reports = res.data.results;
			error = '';
		} else error = errorText(res, t('chat.err.load'));
	}
	onMount(() => void load());

	$effect(() => {
		if (reports) openCount = reports.filter((r) => !r.handled).length;
	});
	const shown = $derived((reports ?? []).filter((r) => showHandled || !r.handled));
	const stamp = (iso: string) => new Date(iso).toLocaleString(getLang(), { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

	async function view(r: Report) {
		viewing = r;
		page = null;
		viewError = '';
		const res = await chatApi.moderationMessages(r.conversation_id);
		if (res.ok && res.data) page = res.data;
		else viewError = errorText(res, t('chat.err.load'));
	}

	async function handle(r: Report) {
		busy = r.id;
		const res = await chatApi.handleReport(r.id);
		busy = '';
		if (res.ok) reports = (reports ?? []).map((x) => (x.id === r.id ? { ...x, handled: true } : x));
		else error = errorText(res, t('chat.err.generic'));
	}

	async function remove(message: Message) {
		if (!confirm(t('chat.msg.delete_confirm'))) return;
		const res = await chatApi.deleteMessage(message.id);
		if (!res.ok) {
			error = errorText(res, t('chat.err.generic'));
			return;
		}
		const gone = (m: Message): Message => (m.id === message.id ? { ...m, deleted: true, body: '', attachments: [], reactions: [] } : m);
		if (page) page = { ...page, messages: page.messages.map(gone) };
		reports = (reports ?? []).map((x) => (x.message.id === message.id ? { ...x, message: gone(x.message) } : x));
	}
</script>

{#if viewing}
	<div class="flex flex-col overflow-hidden rounded-xl border border-line bg-white {heightClass}">
		<div class="flex items-center gap-3 border-b border-line px-3 py-2">
			<button type="button" class="btn btn-ghost btn-sm gap-1.5" onclick={() => (viewing = null)}><ArrowLeft size={15} aria-hidden="true" />{t('chat.mod.back')}</button>
			<h2 class="min-w-0 flex-1 truncate font-display text-base font-extrabold text-ink uppercase">{t('chat.mod.conversation', { title: viewing.conversation_title })}</h2>
		</div>
		{#if viewError}
			<p class="m-auto text-sm text-danger" role="alert">{viewError}</p>
		{:else if !page}
			<p class="m-auto text-sm text-muted" role="status">{t('chat.loading')}</p>
		{:else}
			<MessageList messages={page.messages} hasMoreBefore={false} readonly canDelete ondelete={remove} highlightId={viewing.message.id} />
		{/if}
	</div>
{:else}
	<div class="grid gap-3">
		<div class="flex flex-wrap items-center justify-between gap-3">
			<h2 class="flex items-center gap-2 font-display text-lg font-extrabold text-ink uppercase"><ShieldCheck size={18} aria-hidden="true" />{t('chat.mod.title')}</h2>
			<label class="flex items-center gap-2 text-sm text-ink"><input type="checkbox" class="checkbox checkbox-sm" bind:checked={showHandled} />{t('chat.mod.show_handled')}</label>
		</div>
		{#if error}<p class="text-sm text-danger" role="alert">{error}</p>{/if}
		{#if reports === null && !error}
			<p class="text-sm text-muted" role="status">{t('chat.loading')}</p>
		{:else if !shown.length}
			<p class="card p-6 text-center text-sm text-muted">{t('chat.mod.empty')}</p>
		{/if}
		<ul class="grid gap-3">
			{#each shown as r (r.id)}
				<li class="card grid gap-2 p-4 {r.handled ? 'opacity-70' : ''}">
					<div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
						<span class="badge {r.handled ? 'badge-ghost' : 'badge-warning'} badge-sm">{r.handled ? t('chat.mod.handled') : t('chat.mod.open')}</span>
						<span>{t('chat.mod.in', { title: r.conversation_title })}</span><span aria-hidden="true">·</span>
						<span>{t('chat.mod.reported_by', { name: r.reporter.name })}</span><span aria-hidden="true">·</span><span class="font-mono">{stamp(r.created)}</span>
					</div>
					<blockquote class="rounded-lg border-l-4 border-warn bg-paper px-3 py-2 text-sm text-ink">
						<span class="mb-0.5 block text-xs text-muted"><b class="text-ink">{r.message.author.name}</b> · {stamp(r.message.created)}</span>
						{r.message.deleted ? t('chat.msg.deleted') : r.message.body || '…'}
					</blockquote>
					{#if r.reason}<p class="text-sm text-slate">{r.reason}</p>{/if}
					<div class="flex flex-wrap gap-2">
						<button type="button" class="btn btn-outline btn-sm gap-1.5" onclick={() => view(r)}><Eye size={14} aria-hidden="true" />{t('chat.mod.view')}</button>
						{#if !r.message.deleted}<button type="button" class="btn btn-outline btn-error btn-sm gap-1.5" onclick={() => remove(r.message)}><Trash2 size={14} aria-hidden="true" />{t('chat.mod.delete')}</button>{/if}
						{#if !r.handled}<button type="button" class="btn btn-primary btn-sm gap-1.5" disabled={busy === r.id} onclick={() => handle(r)}><Check size={14} aria-hidden="true" />{t('chat.mod.mark')}</button>{/if}
					</div>
				</li>
			{/each}
		</ul>
	</div>
{/if}
