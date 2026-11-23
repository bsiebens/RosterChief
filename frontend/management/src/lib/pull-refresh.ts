/**
 * Pull to refresh for the installed app (standalone mode has no browser reload button): drag the page down from the very top and let go past
 * the threshold and `refresh` runs. A small pill follows the finger. Works with touch only, never when a scrollable child is not at its top.
 */
export function pullRefresh(node: HTMLElement, refresh: () => Promise<unknown> | unknown) {
	const THRESHOLD = 72;
	let startY = 0;
	let dy = 0;
	let pulling = false;
	let busy = false;
	let pill: HTMLDivElement | null = null;

	const atTop = () => (document.scrollingElement?.scrollTop ?? window.scrollY) <= 0;
	const draw = (y: number, text: string) => {
		if (!pill) {
			pill = document.createElement('div');
			pill.setAttribute('aria-hidden', 'true');
			pill.className = 'fixed left-1/2 z-50 -translate-x-1/2 rounded-full bg-ink px-4 py-1.5 font-mono text-xs text-white shadow-lg transition-transform';
			document.body.appendChild(pill);
		}
		pill.textContent = text;
		pill.style.top = 'calc(env(safe-area-inset-top, 0px) + 8px)';
		pill.style.transform = `translate(-50%, ${Math.min(y, 110) - 48}px)`;
	};
	const clear = () => {
		pill?.remove();
		pill = null;
	};

	function start(e: TouchEvent) {
		if (busy || e.touches.length !== 1 || !atTop()) return;
		startY = e.touches[0].clientY;
		dy = 0;
		pulling = true;
	}
	function move(e: TouchEvent) {
		if (!pulling) return;
		dy = e.touches[0].clientY - startY;
		if (dy <= 0 || !atTop()) {
			pulling = dy > 0;
			clear();
			return;
		}
		draw(dy / 2, dy / 2 >= THRESHOLD / 2 ? '↻' : '↓');
	}
	async function end() {
		if (!pulling) return;
		pulling = false;
		if (dy / 2 >= THRESHOLD / 2) {
			busy = true;
			draw(48, '…');
			try {
				await refresh();
			} finally {
				busy = false;
				clear();
			}
		} else clear();
	}

	node.addEventListener('touchstart', start, { passive: true });
	node.addEventListener('touchmove', move, { passive: true });
	node.addEventListener('touchend', end);
	node.addEventListener('touchcancel', end);
	return {
		destroy() {
			node.removeEventListener('touchstart', start);
			node.removeEventListener('touchmove', move);
			node.removeEventListener('touchend', end);
			node.removeEventListener('touchcancel', end);
			clear();
		}
	};
}

/** Whether the app runs as an installed PWA (home screen) rather than in a browser tab. */
export const isStandalone = () => typeof window !== 'undefined' && (window.matchMedia?.('(display-mode: standalone)').matches || (navigator as Navigator & { standalone?: boolean }).standalone === true);
