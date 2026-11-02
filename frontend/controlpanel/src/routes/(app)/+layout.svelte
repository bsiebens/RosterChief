<script lang="ts">
    import {page} from '$app/state';
    import {goto} from '$app/navigation';
    import {CircleUser, FlaskConical, KeyRound, LogOut, MailX, ShieldCheck, Wrench} from '@lucide/svelte';
    import {auth} from '#lib/api.ts';
    import {NAV} from '#lib/nav.ts';
    import JobStatus from '#lib/components/JobStatus.svelte';
	import ReauthDialog from '#lib/components/ReauthDialog.svelte';

    let {data, children} = $props();

    const a = $derived(data.auth);
    const email = $derived(a.status === 'anonymous' || a.status === 'mfa_pending' ? '' : a.user.email);
    const isSuper = $derived(a.status === 'authenticated' && a.me.level === 'superuser');
    const version = $derived(a.status === 'authenticated' ? a.me.version : '');
    const tabs = $derived(NAV.filter((t) => !t.superuser || isSuper));
    const active = (href: string) => (href === '/' ? page.url.pathname === '/' : page.url.pathname.startsWith(href));

    let menu: HTMLDetailsElement | undefined = $state();

    async function signOut() {
        await auth('DELETE', '/auth/session');
        await goto('/login', {refreshAll: true});
    }
</script>

<svelte:window onclick={(e) => menu?.open && !menu.contains(e.target as Node) && (menu.open = false)}/>

<!-- App shell, as the old control panel's base.html: pinned command bar + breadcrumb strip, <main> scrolls. -->
<div class="flex h-screen flex-col overflow-hidden bg-paper font-sans text-slate">
    <header class="flex h-[52px] shrink-0 items-center gap-5 bg-ink px-5">
        <a class="flex shrink-0 items-center gap-2.5" href="/">
            <img class="h-7 w-7 shrink-0" src="/images/rosterchief-white.svg" alt="" width="28" height="28"/>
            <span class="font-display text-base font-extrabold tracking-[.1em] text-white uppercase">RosterChief</span>
            <span class="font-mono text-[11px] text-on-dark-faint">controlpanel</span>
        </a>

        <nav class="flex h-full font-mono text-xs" aria-label="Sections">
            {#if a.status === 'authenticated'}
                {#each tabs as tab (tab.key)}
                    <a
                            class="flex h-full items-center px-3.5 {active(tab.href) ? 'border-b-2 border-ice bg-steel text-white' : 'text-on-dark-dim hover:text-white'}"
                            href={tab.href}
                            aria-current={active(tab.href) ? 'page' : undefined}>{tab.label}</a
                    >
                {/each}
            {/if}
        </nav>

        <div class="flex-1"></div>

        <JobStatus initial={data.jobsStatus} />

        <details class="account-menu relative shrink-0" bind:this={menu}>
            <summary class="flex cursor-pointer items-center rounded px-1.5 py-1 text-on-dark-dim hover:bg-steel hover:text-white" aria-label="Account">
                <CircleUser size={18} aria-hidden="true"/>
            </summary>
            <div class="absolute top-full right-0 z-20 mt-2 w-64 rounded border border-edge bg-white py-1.5 shadow-lg">
                <div class="truncate border-b border-rule px-3 pb-2 font-mono text-xs text-muted">{email}</div>
                <a class="flex items-center gap-2.5 px-3 py-2 text-sm text-slate hover:bg-rule" href="/account/password" onclick={() => menu && (menu.open = false)}>
                    <KeyRound size={15} aria-hidden="true"/>
                    Change password
                </a>
                <a class="flex items-center gap-2.5 px-3 py-2 text-sm text-slate hover:bg-rule" href="/account/security" onclick={() => menu && (menu.open = false)}>
                    <ShieldCheck size={15} aria-hidden="true"/>
                    Two-factor authentication
                </a>
                <button class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm text-slate hover:bg-rule" onclick={signOut}>
                    <LogOut size={15} aria-hidden="true"/>
                    Sign out
                </button>
            </div>
        </details>

        {#if version}<span class="shrink-0 font-mono text-[11px] text-on-dark-faint" title="Platform version">v{version}</span>{/if}
    </header>

    <div class="flex h-[34px] shrink-0 items-center gap-3 border-b border-edge bg-white px-5 font-mono text-[11px] text-muted">
        <span class="text-ink">controlpanel</span>
        {#each page.url.pathname.split('/').filter(Boolean) as part, i (i)}
            <span aria-hidden="true">/</span><span class="text-ink">{part}</span>
        {/each}
    </div>

    <main class="flex-1 overflow-y-auto">
        <div class="mx-auto flex w-full max-w-[1440px] flex-col gap-4 px-7 py-6">
            {#if data.switches?.maintenance.is_active}
                <div class="flex items-center gap-3 rounded border border-danger-border bg-danger-bg px-4 py-3 text-sm text-club-dark" role="alert" data-testid="maintenance-banner">
                    <Wrench size={18} class="shrink-0" aria-hidden="true" />
                    <div class="min-w-0 flex-1">
                        <span class="font-display font-extrabold tracking-[.04em] uppercase">Platform closed for maintenance.</span>
                        Every club subdomain serves a maintenance page, and the scheduled jobs stand down.
                        {#if data.switches.maintenance.message}<span class="mt-1 block text-xs">“{data.switches.maintenance.message}”</span>{/if}
                    </div>
                    <a class="btn btn-error shrink-0" href="/features">Reopen platform</a>
                </div>
            {/if}

            {#if data.switches?.email_suppression.is_active}
                <div class="flex items-center gap-3 rounded border border-danger-border bg-danger-bg px-4 py-3 text-sm text-club-dark" role="alert" data-testid="email-banner">
                    <MailX size={18} class="shrink-0" aria-hidden="true" />
                    <div class="min-w-0 flex-1">
                        <span class="font-display font-extrabold tracking-[.04em] uppercase">Automated email paused.</span>
                        Notifications, reminders and invoices are not sending. Password reset and other account email still work.
                    </div>
                    <a class="btn btn-error shrink-0" href="/features">Resume email</a>
                </div>
            {/if}

            {#if data.switches?.push_sandbox?.is_active}
                <div class="flex items-center gap-3 rounded border-2 border-warn bg-warn-bg px-4 py-3 text-sm text-ink" role="status" data-testid="sandbox-banner">
                    <FlaskConical size={18} class="shrink-0" aria-hidden="true"/>
                    <div class="min-w-0 flex-1">
                        <span class="font-display font-extrabold tracking-[.04em] uppercase">Push sandbox is on.</span>
                        Nothing is sent to users; pushes and notification emails are captured instead.
                        {#if data.switches.push_sandbox.expires_at}Switches back to live at <strong>{new Date(data.switches.push_sandbox.expires_at).toLocaleString('en-GB', {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'})}</strong>.{/if}
                    </div>
                    {#if isSuper}<a class="btn btn-outline btn-sm shrink-0" href="/push/sandbox">Open sandbox</a>{/if}
                </div>
            {/if}

            {@render children()}
        </div>
    </main>
</div>

<ReauthDialog/>
