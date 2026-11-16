/**
 * Tiptap editor setup for the news body. Kept apart from the Svelte component so it can be loaded lazily (dynamic
 * import) and exercised on its own.
 *
 * The server renders the stored Markdown with Python-Markdown (nl2br, sane_lists, fenced_code, tables) and then
 * sanitises it, so this module only allows what that pipeline can show and serialises to the dialect it understands:
 *  - h2-h4 only (`##` .. `####`; h1 is stripped by the sanitiser),
 *  - a hard break is a single newline (nl2br), paragraphs are separated by a blank line,
 *  - nested lists are indented by four spaces (Python-Markdown ignores two),
 *  - pipe tables with optional `:--:` alignment, and never the HTML fallback of the default serialiser.
 */
import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import HardBreak from '@tiptap/extension-hard-break';
import { BulletList, OrderedList } from '@tiptap/extension-list';
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table';
import { Markdown } from 'tiptap-markdown';

export const HEADING_LEVELS = [2, 3, 4] as const;
const LINK_RE = /^(https?:\/\/|mailto:)\S+$/i;

/** Normalises what a person typed into a link target the server will keep (http, https, mailto), or '' if it can't. */
export function cleanLinkUrl(raw: string): string {
	const s = raw.trim();
	if (!s) return '';
	if (/^[a-z][a-z0-9+.-]*:/i.test(s)) return LINK_RE.test(s) ? s : '';
	if (/^[^\s@/]+@[^\s@/]+\.[^\s@/]+$/.test(s)) return `mailto:${s}`;
	const url = `https://${s}`;
	return LINK_RE.test(url) && s.includes('.') ? url : '';
}

type Align = 'left' | 'center' | 'right' | null;
const alignAttr = {
	align: {
		default: null as Align,
		parseHTML: (el: HTMLElement): Align => {
			const a = (el.style?.textAlign || el.getAttribute('align') || '').toLowerCase();
			return a === 'left' || a === 'center' || a === 'right' ? a : null;
		},
		renderHTML: (attrs: { align?: Align }) => (attrs.align ? { style: `text-align:${attrs.align}` } : {})
	}
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type State = any;

/** Serialiser overrides (tiptap-markdown reads `storage.markdown.serialize` of each extension). */
const serializeHardBreak = (state: State, _node: unknown, parent: State, index: number) => {
	for (let i = index + 1; i < parent.childCount; i++) {
		if (parent.child(i).type !== parent.child(index).type) {
			state.write(state.inTable ? ' ' : '\n');
			// The next line must not be read as a bullet, heading or quote marker.
			const next = parent.child(index + 1);
			if (!state.inTable && next.isText && /^[-+*#>](\s|#|$)/.test(next.text)) state.write('\\');
			return;
		}
	}
};

const serializeBullet = (state: State, node: State) => state.renderList(node, '    ', () => '- ');
const serializeOrdered = (state: State, node: State) => {
	const start = node.attrs.start || 1;
	state.renderList(node, '    ', (i: number) => `${start + i}. `);
};

const delimiterFor = (a: Align) => (a === 'center' ? ':---:' : a === 'right' ? '---:' : a === 'left' ? ':---' : '---');

const serializeTable = (state: State, node: State) => {
	state.inTable = true;
	node.forEach((row: State, _p: number, i: number) => {
		state.write('| ');
		row.forEach((cell: State, _q: number, j: number) => {
			if (j) state.write(' | ');
			const content = cell.firstChild;
			if (content && content.textContent.trim()) {
				const from = state.out.length;
				state.renderInline(content);
				// A literal pipe inside a cell would end the cell.
				state.out = state.out.slice(0, from) + state.out.slice(from).replace(/(?<!\\)\|/g, '\\|');
			}
		});
		state.write(' |');
		state.ensureNewLine();
		if (!i) {
			const cells: string[] = [];
			row.forEach((cell: State) => cells.push(delimiterFor(cell.attrs.align ?? null)));
			state.write(`| ${cells.join(' | ')} |`);
			state.ensureNewLine();
		}
	});
	state.closeBlock(node);
	state.inTable = false;
};

export function buildExtensions() {
	return [
		StarterKit.configure({
			heading: { levels: [...HEADING_LEVELS] },
			underline: false,
			strike: false,
			link: { openOnClick: false, autolink: false, linkOnPaste: true, protocols: ['http', 'https', 'mailto'], HTMLAttributes: { rel: 'noopener noreferrer nofollow' }, isAllowedUri: (url: string) => LINK_RE.test(url) },
			bulletList: false,
			orderedList: false,
			hardBreak: false
		}),
		BulletList.extend({ addStorage: () => ({ markdown: { serialize: serializeBullet, parse: {} } }) }),
		OrderedList.extend({ addStorage: () => ({ markdown: { serialize: serializeOrdered, parse: {} } }) }),
		HardBreak.extend({ addStorage: () => ({ markdown: { serialize: serializeHardBreak, parse: {} } }) }),
		Table.extend({ addStorage: () => ({ markdown: { serialize: serializeTable, parse: {} } }) }).configure({ resizable: false }),
		TableRow,
		TableHeader.extend({ content: 'paragraph', addAttributes() {
			return { ...this.parent?.(), ...alignAttr };
		} }),
		TableCell.extend({ content: 'paragraph', addAttributes() {
			return { ...this.parent?.(), ...alignAttr };
		} }),
		Markdown.configure({ html: false, tightLists: true, bulletListMarker: '-', linkify: false, breaks: true, transformPastedText: true })
	];
}

export type RichEditorOptions = {
	element: HTMLElement;
	id?: string;
	content: string;
	editable: boolean;
	label: string;
	onUpdate: (markdown: string) => void;
	onTransaction: () => void;
};

export function createRichEditor(o: RichEditorOptions): Editor {
	const editor = new Editor({
		element: o.element,
		extensions: buildExtensions(),
		content: o.content,
		editable: o.editable,
		editorProps: { attributes: { ...(o.id ? { id: o.id } : {}), role: 'textbox', 'aria-multiline': 'true', 'aria-label': o.label, class: 'rt-content' } },
		onUpdate: ({ editor: e }) => o.onUpdate(getMarkdown(e)),
		onTransaction: () => o.onTransaction()
	});
	return editor;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const getMarkdown = (e: Editor): string => ((e.storage as any).markdown.getMarkdown() as string).replace(/\s+$/, '');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const mdHtml = (e: Editor, md: string): string => (e.storage as any).markdown.parser.parse(md);

/** Replace the document with Markdown, without emitting an update. */
export function setMarkdown(e: Editor, md: string) {
	e.commands.setContent(md, { emitUpdate: false });
}

const norm = (html: string) => html.replace(/\s+/g, ' ').replace(/> </g, '><').trim();

/**
 * Does the visual editor represent this Markdown faithfully? Compares what the Markdown means (as HTML, through the
 * same parser the editor uses) before and after a trip through the editor. Raw HTML is always reported as lossy.
 */
export function isLossless(e: Editor, md: string): boolean {
	if (!md.trim()) return true;
	// Raw HTML outside fenced code and code spans.
	const bare = md.replace(/^(```|~~~)[\s\S]*?^\1/gm, '').replace(/`[^`\n]*`/g, '');
	if (/<\/?[a-z][a-z0-9-]*(\s[^>]*)?\/?>/i.test(bare) || /<!--/.test(bare)) return false;
	// Parse the source into the editor's document and serialise it again, on a scratch editor.
	const scratch = new Editor({ element: document.createElement('div'), extensions: buildExtensions(), content: md });
	try {
		return norm(mdHtml(scratch, md)) === norm(mdHtml(scratch, getMarkdown(scratch)));
	} finally {
		scratch.destroy();
	}
}
