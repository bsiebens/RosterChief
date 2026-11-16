type InstallPrompt = Event & {
	prompt(): Promise<void>;
	userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
};

export const pwa = $state({ prompt: null as InstallPrompt | null, installed: false });
let registration: Promise<ServiceWorkerRegistration> | undefined;

export function registerPwa() {
	registration ??= navigator.serviceWorker.register('/service-worker.js', { scope: '/', updateViaCache: 'none' }).catch((error: unknown) => {
		registration = undefined;
		throw error;
	});
	return registration;
}

export function startPwa() {
	const standalone = window.matchMedia('(display-mode: standalone)');
	const update = () => { pwa.installed = standalone.matches || (navigator as Navigator & { standalone?: boolean }).standalone === true; };
	const install = (event: Event) => { event.preventDefault(); pwa.prompt = event as InstallPrompt; };
	const installed = () => { pwa.prompt = null; pwa.installed = true; };
	update();
	window.addEventListener('beforeinstallprompt', install);
	window.addEventListener('appinstalled', installed);
	standalone.addEventListener('change', update);
	if ('serviceWorker' in navigator && window.isSecureContext) void registerPwa().catch(() => {});
	return () => {
		window.removeEventListener('beforeinstallprompt', install);
		window.removeEventListener('appinstalled', installed);
		standalone.removeEventListener('change', update);
	};
}
