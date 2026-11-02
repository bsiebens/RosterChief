<script lang="ts">
    import {CircleAlert, CircleCheck} from '@lucide/svelte';
    import type {IssuerSettings} from '#lib/billing.ts';

    let {settings}: { settings: IssuerSettings } = $props();

    const items = $derived([
        {label: 'Issuer name', ok: !!settings.legal_name.trim(), hint: 'Printed at the top of every invoice.'},
        {label: 'Address', ok: !!(settings.address.trim() && settings.zip_code.trim() && settings.city.trim()), hint: 'Street, postal code and city are required on an invoice.'},
        {label: 'Country', ok: !!settings.country, hint: 'Needed to decide when reverse charge applies to EU clubs.'},
        {label: 'VAT number', ok: !!settings.vat_id.trim(), hint: 'Legally required on invoices that charge VAT.'},
        {label: 'IBAN', ok: !!settings.iban, hint: 'Where clubs send their payment.'},
        {label: 'VAT rate', ok: settings.vat_rate !== '' && settings.vat_rate != null, hint: 'The rate applied when a period is opened.'}
    ]);
    const missing = $derived(items.filter((i) => !i.ok).length);
</script>

<section class="card" aria-labelledby="ready-h">
    <div class="card-body gap-3 p-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
            <h2 id="ready-h" class="font-display text-lg leading-none font-extrabold tracking-[.06em] text-ink uppercase">Invoice readiness</h2>
            <span class="badge badge-sm {missing ? 'badge-warning' : 'badge-success'}">{missing ? `${missing} to complete` : 'complete'}</span>
        </div>
        <ul class="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
            {#each items as i (i.label)}
                <li class="flex items-start gap-2 text-sm">
                    {#if i.ok}
                        <CircleCheck size={16} class="mt-0.5 shrink-0 text-ok" aria-hidden="true"/>
                        <span class="sr-only">Done:</span>
                    {:else}
                        <CircleAlert size={16} class="mt-0.5 shrink-0 text-warn" aria-hidden="true"/>
                        <span class="sr-only">Missing:</span>
                    {/if}
                    <span>{i.label}
                        {#if !i.ok}<span class="block text-xs text-muted">{i.hint}</span>{/if}</span>
                </li>
            {/each}
        </ul>
    </div>
</section>
