<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import type { Editor } from '@tiptap/core';
	import { Bold, Code, Italic, Link, List, ListOrdered, Minus, PencilLine, Quote, Redo2, Table, TriangleAlert, Undo2, Unlink } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';

	/**
	 * A visual (WYSIWYG) editor that stores Markdown. The Markdown is the form value; a "Markdown" toggle swaps in the
	 * plain textarea for text the visual editor cannot represent faithfully. Exactly one input carries `name` at a time.
	 * Tiptap is loaded on demand, so until it has arrived (and without JavaScript) the plain textarea is shown.
	 */
	let {
		value = $bindable(''),
		name,
		label,
		lang = 'nl',
		disabled = false,
		invalid = false,
		required = false,
		describedby,
		rows = 12
	}: {
		value?: string;
		name?: string;
		label: string;
		lang?: string;
		disabled?: boolean;
		invalid?: boolean;
		required?: boolean;
		describedby?: string;
		rows?: number;
	} = $props();
	const uid = $props.id();

	type RT = typeof import('./richtext.ts');
	let host = $state<HTMLDivElement>();
	let rt: RT | null = null;
	let editor = $state.raw<Editor | null>(null);
	let tick = $state(0);
	let mode = $state<'visual' | 'markdown'>('visual');
	let lossy = $state(false);
	let lastEmitted = '';
	let linkOpen = $state(false);
	let linkUrl = $state('');
	let linkError = $state(false);

	const viewMode = $derived(editor ? mode : 'markdown');

	onMount(() => {
		let gone = false;
		let made: Editor | null = null;
		(async () => {
			const m = await import('./richtext.ts');
			if (gone || !host) return;
			rt = m;
			lastEmitted = value;
			made = m.createRichEditor({
				element: host,
				id: `${uid}-pm`,
				content: value,
				editable: !disabled,
				label,
				onUpdate: (md) => {
					lastEmitted = md;
					value = md;
				},
				onTransaction: () => tick++
			});
			lossy = !m.isLossless(made, value);
			if (lossy) mode = 'markdown';
			editor = made;
		})();
		return () => {
			gone = true;
			made?.destroy();
			editor = null;
		};
	});

	// The parent changed the text (a reload after saving, a reset): show it without echoing it back or moving the cursor.
	$effect(() => {
		const v = value;
		if (!editor || !rt || mode !== 'visual' || v === lastEmitted) return;
		untrack(() => {
			lastEmitted = v;
			rt!.setMarkdown(editor!, v);
			lossy = !rt!.isLossless(editor!, v);
		});
	});
	$effect(() => {
		editor?.setEditable(!disabled);
	});

	const toVisual = () => {
		if (!editor || !rt) return;
		lastEmitted = value;
		rt.setMarkdown(editor, value);
		lossy = !rt.isLossless(editor, value);
		mode = 'visual';
	};
	const toggleMode = () => (mode === 'visual' ? (mode = 'markdown') : toVisual());

	const st = $derived.by(() => {
		void tick;
		const e = editor;
		if (!e) return null;
		return {
			heading: ([2, 3, 4] as const).find((l) => e.isActive('heading', { level: l })) ?? 0,
			bold: e.isActive('bold'),
			italic: e.isActive('italic'),
			link: e.isActive('link'),
			bullet: e.isActive('bulletList'),
			ordered: e.isActive('orderedList'),
			quote: e.isActive('blockquote'),
			table: e.isActive('table'),
			undo: e.can().undo(),
			redo: e.can().redo()
		};
	});

	const run = (f: (c: ReturnType<Editor['chain']>) => ReturnType<Editor['chain']>) => editor && f(editor.chain().focus()).run();
	const setHeading = (ev: Event) => {
		const level = Number((ev.currentTarget as HTMLSelectElement).value);
		run((c) => (level ? c.setHeading({ level: level as 2 | 3 | 4 }) : c.setParagraph()));
	};

	const openLink = () => {
		if (!editor) return;
		linkUrl = (editor.getAttributes('link').href as string | undefined) ?? '';
		linkError = false;
		linkOpen = true;
	};
	const closeLink = () => {
		linkOpen = false;
		editor?.commands.focus();
	};
	const applyLink = () => {
		if (!editor || !rt) return;
		const href = rt.cleanLinkUrl(linkUrl);
		if (!href) {
			linkError = true;
			return;
		}
		const ch = editor.chain().focus();
		if (editor.state.selection.empty && !editor.isActive('link')) ch.insertContent({ type: 'text', text: linkUrl.trim(), marks: [{ type: 'link', attrs: { href } }] }).run();
		else ch.extendMarkRange('link').setLink({ href }).run();
		linkOpen = false;
	};
	const removeLink = () => {
		run((c) => c.extendMarkRange('link').unsetLink());
		linkOpen = false;
	};
	const linkKeys = (ev: KeyboardEvent) => {
		if (ev.key === 'Enter') {
			ev.preventDefault();
			applyLink();
		} else if (ev.key === 'Escape') {
			ev.preventDefault();
			ev.stopPropagation();
			closeLink();
		}
	};

	const btn = 'btn btn-ghost btn-sm btn-square';
</script>

<div class="form-control w-full">
	<div class="label flex items-end justify-between gap-3">
		{#if viewMode === 'markdown'}
			<label class="label-text" for="{uid}-ta">{label}</label>
		{:else}
			<span class="label-text" id="{uid}-lbl">{label}</span>
		{/if}
		<button type="button" class="btn btn-ghost btn-xs gap-1.5" aria-pressed={viewMode === 'markdown'} disabled={disabled || !editor} onclick={toggleMode}>
			{#if viewMode === 'markdown'}<PencilLine size={13} aria-hidden="true" /> {t('news.editor.visual')}{:else}<Code size={13} aria-hidden="true" /> {t('news.editor.markdown')}{/if}
		</button>
	</div>

	{#if lossy && viewMode !== 'markdown'}
		<p class="mb-2 flex items-start gap-2 rounded-lg border border-warn-text/40 bg-subhead px-3 py-2 text-xs text-warn-text" role="status"><TriangleAlert size={14} class="mt-px shrink-0" aria-hidden="true" /> {t('news.editor.lossy')}</p>
	{:else if lossy}
		<p class="mb-2 flex items-start gap-2 rounded-lg border border-warn-text/40 bg-subhead px-3 py-2 text-xs text-warn-text" role="status"><TriangleAlert size={14} class="mt-px shrink-0" aria-hidden="true" /> {t('news.editor.lossy_markdown')}</p>
	{/if}

	<!-- The visual editor stays mounted while hidden (its state and undo history survive a toggle); only one input has the form name. -->
	<div class="rt {invalid ? 'rt-invalid' : ''}" hidden={viewMode !== 'visual'}>
		<div class="rt-bar" role="toolbar" aria-label={t('news.editor.toolbar', { label })} aria-controls="{uid}-pm">
			<select class="select rt-heading" aria-label={t('news.editor.heading')} value={String(st?.heading ?? 0)} onchange={setHeading} disabled={disabled || !editor}>
				<option value="0">{t('news.editor.paragraph')}</option>
				<option value="2">{t('news.editor.h2')}</option>
				<option value="3">{t('news.editor.h3')}</option>
				<option value="4">{t('news.editor.h4')}</option>
			</select>
			<span class="rt-sep" aria-hidden="true"></span>
			<button type="button" class={btn} aria-label={t('news.editor.bold')} title={t('news.editor.bold')} aria-pressed={st?.bold ?? false} disabled={disabled || !editor} onclick={() => run((c) => c.toggleBold())}><Bold size={15} aria-hidden="true" /></button>
			<button type="button" class={btn} aria-label={t('news.editor.italic')} title={t('news.editor.italic')} aria-pressed={st?.italic ?? false} disabled={disabled || !editor} onclick={() => run((c) => c.toggleItalic())}><Italic size={15} aria-hidden="true" /></button>
			<button type="button" class={btn} aria-label={t('news.editor.link')} title={t('news.editor.link')} aria-pressed={st?.link ?? false} aria-expanded={linkOpen} disabled={disabled || !editor} onclick={() => (linkOpen ? closeLink() : openLink())}><Link size={15} aria-hidden="true" /></button>
			<span class="rt-sep" aria-hidden="true"></span>
			<button type="button" class={btn} aria-label={t('news.editor.bullets')} title={t('news.editor.bullets')} aria-pressed={st?.bullet ?? false} disabled={disabled || !editor} onclick={() => run((c) => c.toggleBulletList())}><List size={15} aria-hidden="true" /></button>
			<button type="button" class={btn} aria-label={t('news.editor.numbers')} title={t('news.editor.numbers')} aria-pressed={st?.ordered ?? false} disabled={disabled || !editor} onclick={() => run((c) => c.toggleOrderedList())}><ListOrdered size={15} aria-hidden="true" /></button>
			<button type="button" class={btn} aria-label={t('news.editor.quote')} title={t('news.editor.quote')} aria-pressed={st?.quote ?? false} disabled={disabled || !editor} onclick={() => run((c) => c.toggleBlockquote())}><Quote size={15} aria-hidden="true" /></button>
			<button type="button" class={btn} aria-label={t('news.editor.rule')} title={t('news.editor.rule')} disabled={disabled || !editor} onclick={() => run((c) => c.setHorizontalRule())}><Minus size={15} aria-hidden="true" /></button>
			<button type="button" class={btn} aria-label={t('news.editor.table')} title={t('news.editor.table')} disabled={disabled || !editor || st?.table} onclick={() => run((c) => c.insertTable({ rows: 3, cols: 3, withHeaderRow: true }))}><Table size={15} aria-hidden="true" /></button>
			<span class="rt-sep" aria-hidden="true"></span>
			<button type="button" class={btn} aria-label={t('news.editor.undo')} title={t('news.editor.undo')} disabled={disabled || !st?.undo} onclick={() => run((c) => c.undo())}><Undo2 size={15} aria-hidden="true" /></button>
			<button type="button" class={btn} aria-label={t('news.editor.redo')} title={t('news.editor.redo')} disabled={disabled || !st?.redo} onclick={() => run((c) => c.redo())}><Redo2 size={15} aria-hidden="true" /></button>
		</div>

		{#if st?.table && !disabled}
			<div class="rt-bar rt-bar-table" role="toolbar" aria-label={t('news.editor.table_tools')}>
				<button type="button" class="btn btn-ghost btn-xs" onclick={() => run((c) => c.addRowAfter())}>{t('news.editor.row_add')}</button>
				<button type="button" class="btn btn-ghost btn-xs" onclick={() => run((c) => c.deleteRow())}>{t('news.editor.row_del')}</button>
				<button type="button" class="btn btn-ghost btn-xs" onclick={() => run((c) => c.addColumnAfter())}>{t('news.editor.col_add')}</button>
				<button type="button" class="btn btn-ghost btn-xs" onclick={() => run((c) => c.deleteColumn())}>{t('news.editor.col_del')}</button>
				<button type="button" class="btn btn-ghost btn-xs text-error" onclick={() => run((c) => c.deleteTable())}>{t('news.editor.table_del')}</button>
			</div>
		{/if}

		{#if linkOpen}
			<div class="rt-bar rt-bar-link" role="group" aria-label={t('news.editor.link')}>
				<input
					class="input rt-url"
					type="text"
					inputmode="url"
					autocomplete="off"
					placeholder="https://"
					aria-label={t('news.editor.link_url')}
					aria-invalid={linkError ? 'true' : undefined}
					aria-describedby={linkError ? `${uid}-linkerr` : undefined}
					bind:value={linkUrl}
					oninput={() => (linkError = false)}
					onkeydown={linkKeys}
					{@attach (n) => n.focus()} />
				<button type="button" class="btn btn-primary btn-sm" onclick={applyLink}>{t('news.editor.link_apply')}</button>
				{#if st?.link}<button type="button" class="btn btn-outline btn-sm gap-1.5" onclick={removeLink}><Unlink size={13} aria-hidden="true" /> {t('news.editor.link_remove')}</button>{/if}
				<button type="button" class="btn btn-ghost btn-sm" onclick={closeLink}>{t('common.cancel')}</button>
				{#if linkError}<span id="{uid}-linkerr" class="basis-full text-xs text-error" role="alert">{t('news.editor.link_invalid')}</span>{/if}
			</div>
		{/if}

		<div class="rt-host" {lang} bind:this={host}></div>
	</div>
	{#if viewMode === 'visual' && name}<input type="hidden" {name} {value} />{/if}

	{#if viewMode === 'markdown'}
		<textarea
			id="{uid}-ta"
			class="textarea textarea-bordered w-full font-mono text-sm {invalid ? 'textarea-error' : ''}"
			{name}
			{rows}
			{lang}
			{disabled}
			{required}
			aria-invalid={invalid ? 'true' : undefined}
			aria-describedby={describedby}
			bind:value></textarea>
	{/if}
</div>

<style>
	.rt {
		border: 1px solid var(--color-edge);
		border-radius: 0.5rem;
		background: #fff;
		overflow: hidden;
	}
	.rt:focus-within {
		outline: 2px solid var(--color-club);
		outline-offset: -1px;
		border-color: var(--color-club);
	}
	.rt-invalid {
		border-color: var(--color-danger);
	}
	.rt-bar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.25rem;
		padding: 0.375rem;
		border-bottom: 1px solid var(--color-line);
		background: var(--color-subhead);
	}
	.rt-bar-table,
	.rt-bar-link {
		background: #fff;
	}
	.rt-sep {
		width: 1px;
		height: 1.25rem;
		margin-inline: 0.25rem;
		background: var(--color-line);
	}
	.rt-heading {
		width: auto;
		height: 1.75rem;
		padding-block: 0;
		font-size: 0.75rem;
	}
	.rt-url {
		flex: 1 1 14rem;
		height: 1.75rem;
		padding-block: 0;
	}
	.rt-bar button[aria-pressed='true'] {
		background: var(--color-primary);
		color: var(--color-primary-content);
	}
	.rt-bar button:focus-visible {
		outline: 2px solid var(--color-club);
		outline-offset: 1px;
	}
	.rt-host :global(.rt-content) {
		min-height: 14rem;
		max-height: 32rem;
		overflow-y: auto;
		padding: 0.6rem 0.75rem;
		font-size: 0.875rem;
		color: var(--color-slate);
		outline: none;
	}
	.rt-host :global(.rt-content[contenteditable='false']) {
		background: var(--color-subhead);
	}
	.rt-host :global(p),
	.rt-host :global(ul),
	.rt-host :global(ol),
	.rt-host :global(blockquote),
	.rt-host :global(table),
	.rt-host :global(pre) {
		margin-block: 0.6rem;
	}
	.rt-host :global(.rt-content > :first-child) {
		margin-top: 0;
	}
	.rt-host :global(h2),
	.rt-host :global(h3),
	.rt-host :global(h4) {
		margin-block: 1rem 0.4rem;
		font-family: var(--font-display);
		font-weight: 800;
		text-transform: uppercase;
		color: var(--color-ink);
	}
	.rt-host :global(h2) {
		font-size: 1.25rem;
	}
	.rt-host :global(h3) {
		font-size: 1.1rem;
	}
	.rt-host :global(h4) {
		font-size: 1rem;
	}
	.rt-host :global(ul) {
		list-style: disc;
		padding-left: 1.4rem;
	}
	.rt-host :global(ol) {
		list-style: decimal;
		padding-left: 1.4rem;
	}
	.rt-host :global(li > p) {
		margin-block: 0.15rem;
	}
	.rt-host :global(blockquote) {
		border-left: 3px solid var(--color-club);
		padding-left: 0.9rem;
		color: var(--color-muted);
	}
	.rt-host :global(a) {
		color: var(--color-club-dark);
		text-decoration: underline;
		cursor: text;
	}
	.rt-host :global(hr) {
		margin-block: 1rem;
		border: 0;
		border-top: 1px solid var(--color-edge);
	}
	.rt-host :global(code) {
		font-family: var(--font-mono);
		font-size: 0.8125rem;
		background: var(--color-subhead);
		padding: 0.05rem 0.25rem;
		border-radius: 0.25rem;
	}
	.rt-host :global(pre) {
		background: var(--color-subhead);
		padding: 0.5rem 0.75rem;
		border-radius: 0.375rem;
		overflow-x: auto;
	}
	.rt-host :global(pre code) {
		padding: 0;
		background: none;
	}
	.rt-host :global(table) {
		border-collapse: collapse;
		width: 100%;
		table-layout: fixed;
	}
	.rt-host :global(th),
	.rt-host :global(td) {
		border: 1px solid var(--color-line);
		padding: 0.25rem 0.5rem;
		vertical-align: top;
		position: relative;
		min-width: 2rem;
	}
	.rt-host :global(th) {
		background: var(--color-subhead);
		text-align: left;
	}
	.rt-host :global(td > p),
	.rt-host :global(th > p) {
		margin: 0;
	}
	.rt-host :global(.selectedCell) {
		background: color-mix(in srgb, var(--color-club) 12%, white);
	}
</style>
