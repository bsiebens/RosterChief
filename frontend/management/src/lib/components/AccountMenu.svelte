<script lang="ts">
	import { CircleUser, KeyRound, LogOut, ShieldCheck } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import { signOut } from '#lib/session.ts';
	import LangSwitcher from './LangSwitcher.svelte';

	/** Light dropdown for the shells without a sidebar (club picker, no-access, MFA enrolment). */
	let { email, dark = false }: { email: string; dark?: boolean } = $props();

	let menu: HTMLDetailsElement | undefined = $state();
	const close = () => menu && (menu.open = false);
</script>

<svelte:window onclick={(e) => menu?.open && !menu.contains(e.target as Node) && close()} onkeydown={(e) => e.key === 'Escape' && close()} />

<details class="account-menu relative shrink-0" bind:this={menu}>
	<summary class="flex min-h-10 min-w-10 cursor-pointer items-center justify-center rounded {dark ? 'text-sidebar-fg hover:bg-sidebar-chip' : 'text-muted hover:bg-rule hover:text-ink'}" aria-label={t('shell.menu.account')}>
		<CircleUser size={20} aria-hidden="true" />
	</summary>
	<div class="absolute top-full right-0 z-20 mt-2 w-64 rounded border border-edge bg-white py-1.5 shadow-lg">
		<div class="truncate border-b border-rule px-3 pb-2 font-mono text-xs text-muted">{email}</div>
		<a class="flex items-center gap-2.5 px-3 py-2 text-sm text-slate hover:bg-rule" href="/account/password" onclick={close}>
			<KeyRound size={15} aria-hidden="true" />
			{t('shell.menu.password')}
		</a>
		<a class="flex items-center gap-2.5 px-3 py-2 text-sm text-slate hover:bg-rule" href="/account/security" onclick={close}>
			<ShieldCheck size={15} aria-hidden="true" />
			{t('shell.menu.security')}
		</a>
		<div class="flex items-center justify-between border-t border-rule px-3 py-2">
			<span class="text-xs text-muted">{t('shell.menu.language')}</span>
			<LangSwitcher />
		</div>
		<button class="flex w-full items-center gap-2.5 border-t border-rule px-3 py-2 text-left text-sm text-slate hover:bg-rule" onclick={signOut}>
			<LogOut size={15} aria-hidden="true" />
			{t('shell.menu.logout')}
		</button>
	</div>
</details>
