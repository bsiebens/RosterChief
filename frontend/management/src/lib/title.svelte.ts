import { page } from '$app/state';

/** '{page} · {club name}' (or RosterChief on the bare domain), for <svelte:head><title>. */
export const pageTitle = (title: string) => `${title} · ${page.data.mode === 'club' && page.data.branding ? page.data.branding.name : 'RosterChief'}`;
