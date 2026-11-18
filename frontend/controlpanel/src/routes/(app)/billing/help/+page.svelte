<script lang="ts">
    // Static explainer: how billing works. Keep it in step with billing/services (dues.py, reminders.py) and the beat
    // schedule in rosterchief/celery.py; the job times below are the code defaults and can be changed on the Jobs page.
    const SECTIONS = [
        ['cycles', 'Billing cycles'],
        ['timeline', 'From invoice to archive'],
        ['renewal', 'Auto-renew and lapsed clubs'],
        ['trials', 'Trials'],
        ['switching', 'Switching plans mid-cycle'],
        ['unpaid', 'Unpaid periods'],
        ['vat', 'VAT and reverse charge'],
        ['invoices', 'Invoices and numbers'],
        ['correcting', 'Correcting an invoice'],
        ['payments', 'Payments, waiving and reminders'],
        ['glossary', 'Glossary']
    ] as const;
</script>

<svelte:head><title>How billing works · RosterChief</title></svelte:head>

<div class="grid items-start gap-6 xl:grid-cols-[14rem_1fr]">
    <nav class="card sticky top-0 hidden p-3 xl:block" aria-label="On this page">
        <div class="mb-2 font-mono text-[10px] tracking-[.08em] text-muted uppercase">On this page</div>
        <ul class="flex flex-col gap-0.5 text-sm">
            {#each SECTIONS as [id, label] (id)}
                <li><a class="block rounded px-2 py-1 text-slate hover:bg-rule" href="#{id}">{label}</a></li>
            {/each}
        </ul>
    </nav>

    <article class="flex max-w-fit flex-col gap-4 text-sm leading-relaxed text-slate">
        <div class="card">
            <div class="card-body gap-2">
                <p>
                    RosterChief invoices its clubs. A club is on a <strong>plan</strong>, which decides what it can use. When a club is <strong>billed</strong>, it also has a stream of <strong>periods</strong>: each one has a price, an invoice, a
                    deadline and payments. This page explains how those parts fit together. Everything here is automatic; the scheduled jobs do the work, and you step in for exceptions.
                </p>
            </div>
        </div>

        <section id="cycles" class="card scroll-mt-4">
            <div class="card-body gap-3">
                <h2 class="card-title">Billing cycles</h2>
                <p>Every club has its <strong>own cycle</strong>, anchored on the day it was subscribed (an “anniversary” cycle). A period starts that day and lasts the <em>period length</em> of the billing cycle it is billed on (for example 1 month, 3 months or 12 months). The next period starts the
                    day after the previous one ends, so there are never gaps or overlaps.</p>
                <ul class="list-disc space-y-1 pl-5">
                    <li><strong>Price and VAT are fixed when a period opens.</strong> Changing a plan's price later (prices are dated, a new price is a new row) only affects periods opened after the new price starts.</li>
                    <li><strong>Prices exclude VAT.</strong> VAT is added on top, see below.</li>
                    <li><strong>Plan changes are prorated.</strong> Switching a billed club's plan mid-cycle shortens the running period, credits the unused part and starts a new cycle on the new plan, see <a class="link" href="#switching">Switching
                        plans mid-cycle</a>. Unbilled clubs just get the new features.
                    </li>
                    <li>A plan can offer several <strong>billing cycles</strong> (for example monthly, quarterly and yearly), each with its own price. Every cycle has three timing settings (Plans &amp; pricing): <em>period length</em>, <em>invoice N days before the period starts</em> and <em>grace days</em>. You choose the cycle per club when you start billing it or change its plan; the order of plans and cycles is yours to set with the arrows.</li>
                </ul>
            </div>
        </section>

        <section id="timeline" class="card scroll-mt-4">
            <div class="card-body gap-4">
                <h2 class="card-title">From invoice to archive</h2>
                <p>This is the life of one period for a club that does not pay. Dates move with the plan's settings; the daily jobs below run at these times by default and can be changed on the Jobs page.</p>
                <ol class="relative flex flex-col gap-0 border-l-2 border-line pl-6">
                    {#each [
                        ['Invoice opens', 'N days before the period starts (the plan’s “invoice lead”). The period is created, priced and invoiced. Job: Renew subscriptions, 04:00.', 'bg-ice'],
                        ['Period starts', 'The club is using the period it was invoiced for. Reminders have started (see below). Job: Send billing reminders, 05:00.', 'bg-ink'],
                        ['Grace ends', 'Grace days after the period start, or after the invoice date if that is later. After this an unpaid period is overdue.', 'bg-warn'],
                        ['Downgrade', 'Next 05:30 run: the club is moved to the default (free) plan. Members above that plan’s limit stay, but nobody can add more. Job: Downgrade overdue clubs.', 'bg-warn'],
                        ['Archive', 'After the configured number of days (Invoicing tab, default 30) and only if the club’s auto-archive is on: the subdomain stops resolving, the data is kept. Job: Archive overdue clubs, 06:00.', 'bg-club']
                    ] as [title, text, color] (title)}
                        <li class="relative pb-5 last:pb-0">
                            <span class="absolute top-1 -left-[1.9rem] h-3 w-3 rounded-full border-2 border-white {color}"></span>
                            <div class="font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase">{title}</div>
                            <p>{text}</p>
                        </li>
                    {/each}
                </ol>
                <p><strong>Paying or waiving</strong> the overdue periods puts the club back on its plan immediately; you do not have to wait for a job.</p>
            </div>
        </section>

        <section id="renewal" class="card scroll-mt-4">
            <div class="card-body gap-3">
                <h2 class="card-title">Auto-renew and lapsed clubs</h2>
                <ul class="list-disc space-y-1 pl-5">
                    <li><strong>Auto-renew on</strong> (default): the next period is opened and invoiced <em>in advance</em>, once the current one ends within the plan's invoice lead. A club never goes off its plan between periods.</li>
                    <li><strong>Auto-renew off</strong>: nothing is opened in advance, so the club's period will end. Once it has ended, the next period is opened anyway (“lapsed”), starting the day after the old one ended, so the gap is billed. Its
                        grace is counted from the day it was invoiced, so the club still has the full grace days to pay. From there the normal overdue steps apply.
                    </li>
                    <li>“Open next period now” on the Cycles tab does the same thing early by hand.</li>
                    <li>If the renewal job itself fails, tomorrow's run picks the club up again; failures appear on the Jobs page and in the dashboard alerts.</li>
                </ul>
            </div>
        </section>

        <section id="trials" class="card scroll-mt-4">
            <div class="card-body gap-3">
                <h2 class="card-title">Trials</h2>
                <p>A trial belongs to a <strong>club</strong>, not to a plan: any plan can be trialled. Tick “Start with a free trial” when you start billing for a club and choose the number of days (default 30).</p>
                <ul class="list-disc space-y-1 pl-5">
                    <li>The club gets the chosen plan's features <strong>at once</strong>, for free, for the number of days chosen. The trial is a period of its own (marked “trial”, zero amount, nothing to pay).</li>
                    <li>The first <strong>paid</strong> period starts the day after the trial ends and is invoiced automatically in advance, like any renewal. After the trial the club stays on the <strong>same plan</strong>; use “Change plan” on the
                        club page if it should move to another one.
                    </li>
                    <li>The club keeps its plan's features through the whole changeover. It is not moved to the free plan when the trial ends, and it does not get a different plan early.</li>
                    <li>A trial can only be given when billing starts. For a club that is already billed, use a per-club override on the club's Features card instead (a feature granted for a limited time).</li>
                </ul>
            </div>
        </section>

        <section id="switching" class="card scroll-mt-4">
            <div class="card-body gap-3">
                <h2 class="card-title">Switching plans mid-cycle</h2>
                <p>When a <strong>billed</strong> club moves to another plan (club page, Plan card, “Change plan”), the money is settled on the spot and a new cycle starts. Nothing is edited after the fact; everything is documented by invoices and
                    credit notes.</p>
                <ol class="list-decimal space-y-1 pl-5">
                    <li><strong>The old period is shortened</strong> so that it ends the day before the switch date, and its price is <strong>prorated by days</strong> (VAT at the period's own rate). The days used are charged, the unused days are
                        not.
                    </li>
                    <li>A <strong>credit note</strong> is issued for the unused part. The original invoice is <strong>never edited</strong>: the invoice and the credit note together show what is owed.</li>
                    <li>If the club <strong>already paid more</strong> than the prorated amount, the difference becomes <strong>club credit</strong>. Club credit is applied automatically to the new invoice and to later invoices, and is carried
                        forward until used. <strong>The system never refunds</strong>.
                    </li>
                    <li>A <strong>new full period</strong> of the new plan starts <strong>on the switch date</strong> at the new plan's price and is invoiced immediately (a normal gapless invoice number). Available credit is applied to it. This
                        <strong>moves the club's anniversary</strong>: later renewals follow the new start date.
                    </li>
                    <li>Periods already <strong>invoiced in advance</strong> (the next renewal) are <strong>cancelled</strong> with a full credit note; their payments become club credit. The invoice is kept, marked cancelled.</li>
                    <li><strong>Trials end</strong> when the club switches during one; the new plan starts as a paid cycle.</li>
                </ol>
                <div class="rounded border border-rule bg-paper p-3">
                    <div class="mb-1 font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase">Example: upgrade from 100 per year to 300 per year</div>
                    <p>The club pays <span class="font-mono">100.00 + 21.00 VAT = 121.00</span> for 2026-01-01 to 2026-12-31 and switches on <strong>2026-07-02</strong>. It used 182 of 365 days.</p>
                    <ul class="mt-1 list-disc space-y-0.5 pl-5">
                        <li>Prorated old period: <span class="font-mono">49.86 + 10.47 = 60.33</span>. Credit note for the rest: <span class="font-mono">60.67</span>. Since the 121.00 was paid, <span class="font-mono">60.67</span> becomes club
                            credit.
                        </li>
                        <li>New period 2026-07-02 to 2027-07-01: <span class="font-mono">300.00 + 63.00 VAT = 363.00</span>, invoiced at once.</li>
                        <li>Credit applied <span class="font-mono">60.67</span>, so the club has to pay <strong class="font-mono">302.33</strong>. Credit left: <span class="font-mono">0.00</span>.</li>
                        <li>If the old period had <em>not</em> been paid: same credit note, no club credit, and the club pays the full <span class="font-mono">363.00</span>.</li>
                        <li>A downgrade works the same way. From 300 (paid 363.00) to 40: credit note <span class="font-mono">182.00</span>, new invoice <span class="font-mono">48.40</span> paid entirely from credit, <span
                                class="font-mono">133.60</span> of credit remains.
                        </li>
                    </ul>
                </div>
                <ul class="list-disc space-y-1 pl-5">
                    <li><strong>Preview first.</strong> The dialog calculates every figure (prorated amounts, credit notes, credit applied, amount payable, credit left) before you confirm. Nothing is written until you click “Change plan and start a
                        new cycle”.
                    </li>
                    <li><strong>Switch date.</strong> Defaults to today. It can be back-dated to the day after the current period's start, never into the future. Features change immediately either way.</li>
                    <li><strong>Who.</strong> Only superusers can change a billed club's plan. Staff can see the result on the club page and in Billing.</li>
                    <li><strong>Need a refund?</strong> The system does not pay money back. Refund the club outside the system (bank transfer), and note it, for example in the club's billing notes. The club credit stays on the ledger until you agree
                        with the club how to handle it.
                    </li>
                    <li>A period can only be prorated once. A plan needs a price on the switch date, and the club cannot be switched to the plan it is already on.</li>
                </ul>
            </div>
        </section>

        <section id="unpaid" class="card scroll-mt-4">
            <div class="card-body gap-3">
                <h2 class="card-title">Unpaid periods: downgrade first, archive later</h2>
                <ul class="list-disc space-y-1 pl-5">
                    <li><strong>Downgrading</strong> always happens once a period is overdue. It is reversible and keeps the club usable on the free plan's limits.</li>
                    <li><strong>Archiving</strong> happens only if the club's <em>auto-archive</em> is on, and only after the “days between downgrade and archive” setting (Invoicing tab; 0 = archive right after downgrading).</li>
                    <li><strong>Where is auto-archive set?</strong> Per club: chosen when you start billing (default on) and changeable on the club page, Billing card, “Archive when overdue”. Turn it off for clubs that should never be archived
                        automatically; they are still downgraded.
                    </li>
                    <li>An archived club can be restored from the club page. Opening its next period (or reactivating it) restores it and bills the time it was away.</li>
                </ul>
            </div>
        </section>

        <section id="vat" class="card scroll-mt-4">
            <div class="card-body gap-3">
                <h2 class="card-title">VAT and reverse charge</h2>
                <ul class="list-disc space-y-1 pl-5">
                    <li>The <strong>standard rate</strong> is set on the Invoicing tab (default 21%) and added on top of the plan price: 50.00 + 21% = 60.50 payable.</li>
                    <li><strong>Reverse charge</strong> (0% VAT, noted on the invoice) applies when the club has a VAT number <em>and</em> its legal country is an EU member state different from the issuer's country. It needs the issuer's country on
                        the Invoicing tab; the readiness checklist there warns when it is missing.
                    </li>
                    <li>Free and trial periods show the same rate as any other (for example 21%), with a VAT amount of 0.00.</li>
                    <li>The rate and amounts are <strong>frozen on the period</strong> when it opens. Later changes to the rate do not touch existing periods or invoices.</li>
                    <li>Everything that is “owed” or “paid” works on the <strong>total including VAT</strong>.</li>
                </ul>
            </div>
        </section>

        <section id="invoices" class="card scroll-mt-4">
            <div class="card-body gap-3">
                <h2 class="card-title">Invoices and numbers</h2>
                <ul class="list-disc space-y-1 pl-5">
                    <li>An invoice is created automatically when a period opens. It stores a <strong>snapshot</strong> of the issuer details, the club's details and the amounts, so editing settings or the club later never changes an invoice that has
                        been issued. Payments received are shown live.
                    </li>
                    <li><strong>Numbers</strong> are automatic and gapless, in a <strong>format you set</strong> on the Invoicing tab (a <em>number pattern</em>). Examples: <span class="font-mono">INV-{'{YYYY}'}-{'{NNNNN}'}</span> gives <span
                            class="font-mono">INV-2026-00001</span>; <span class="font-mono">{'{YY}{MM}'}-{'{NNN}'}</span> gives <span class="font-mono">2611-001</span>; <span class="font-mono">FAC/{'{YYYY}'}/{'{NNNN}'}</span> gives <span
                            class="font-mono">FAC/2026/0001</span>. A number is only taken when an invoice is successfully created, so failures never leave holes.
                    </li>
                    <li><strong>Placeholders:</strong> <span class="font-mono">{'{YYYY}'}</span> year (4 digits), <span class="font-mono">{'{YY}'}</span> year (2 digits), <span class="font-mono">{'{MM}'}</span> month, and exactly one sequence
                        placeholder: <span class="font-mono">{'{N}'}</span> without padding or <span class="font-mono">{'{NNNNN}'}</span> padded with zeros to the number of N's. Besides placeholders you can use letters, digits, spaces and <span
                                class="font-mono">/ - _ . #</span>.
                    </li>
                    <li><strong>The counter restarts</strong> with the finest date placeholder in the pattern: every month with <span class="font-mono">{'{MM}'}</span>, every year with <span class="font-mono">{'{YYYY}'}</span> or <span
                            class="font-mono">{'{YY}'}</span>, never without a date. The counter is independent of the pattern text: changing the pattern keeps counting, so numbers are never repeated or skipped. A new pattern only applies to invoices
                        created after saving; existing invoices keep their numbers.
                    </li>
                    <li><strong>Set next number:</strong> on the Invoicing tab you can move the counter forward, for example when you take over from another system. It can never go back.</li>
                    <li><strong>Custom numbers</strong> are only for numbers that must follow another system, and only when a person acts by hand: when starting billing, opening the next period now, changing a billed club's plan, issuing an invoice
                        created outside the system, or voiding and reissuing. Renewals are always automatic. A custom number never touches the automatic counter, cannot look like the automatic format, must be unique (voided invoices count) and can
                        never be edited afterwards. To fix a wrong invoice, <a class="link" href="#correcting">void and reissue</a> it.
                    </li>
                    <li><strong>Credit notes</strong> correct an invoice without editing it (see Switching plans). They have <strong>their own number pattern and counter</strong> (default <span class="font-mono">CN-2026-00001</span>), always
                        automatic, and are immutable once issued. They are listed on the Credit notes tab with a PDF each.
                    </li>
                    <li><strong>Sending:</strong> “Send by email” mails the PDF to the club's administrators. “Mark as sent” records that you sent it yourself.</li>
                    <li>The PDF shows the issuer, the club, net / VAT / total, the due date, and payment details with the invoice number as the payment reference.</li>
                </ul>
            </div>
        </section>

        <section id="correcting" class="card scroll-mt-4">
            <div class="card-body gap-3">
                <h2 class="card-title">Correcting an invoice</h2>
                <p>An invoice is never edited. When it contains a mistake (a wrong address, a wrong VAT number), a superuser uses <strong>Void &amp; reissue</strong> in the period's details on the Periods tab, with a required reason.</p>
                <ul class="list-disc space-y-1 pl-5">
                    <li><strong>What happens:</strong> the invoice is marked <em>VOID</em> and keeps its number forever; a <strong>credit note for the full amount</strong> is issued (an invoice of 0.00 is voided without one); and a <strong>replacement
                        invoice</strong> for the same period is issued with the next automatic number. Numbers are never reused or edited, so the paper trail stays complete. The voided PDF stays available and names its replacement.
                    </li>
                    <li><strong>Documents only:</strong> the credit note is a document. Payments, the period's amounts, its balance and the club's credit do not change, and the credit note creates no club credit.</li>
                    <li><strong>Send it again:</strong> the replacement counts as not sent. Email it or mark it as sent, like any other invoice.</li>
                    <li><strong>Not available</strong> when the invoice already has credit notes (for example after a plan change; that credit note already documents the adjustment) or when the period was cancelled.</li>
                    <li><strong>Custom number:</strong> only use it when the replacement must follow the numbering of an external system. It cannot look like the automatic format, must be unique and cannot be changed afterwards. Otherwise leave it on
                        automatic.
                    </li>
                    <li>Voiding again voids the replacement too, so check the invoice before confirming. Voided invoices are listed on the period and are found by searching their number.</li>
                </ul>
            </div>
        </section>

        <section id="payments" class="card scroll-mt-4">
            <div class="card-body gap-3">
                <h2 class="card-title">Payments, waiving and reminders</h2>
                <ul class="list-disc space-y-1 pl-5">
                    <li><strong>Record payment</strong> adds a payment (a partial payment is fine). A period is paid when payments reach the total; a payment larger than the open balance is refused. Removing a payment re-settles the period.</li>
                    <li><strong>Waive</strong> forgives an unpaid period that has no payments. It is for exceptions and cannot be undone.</li>
                    <li><strong>Reminders</strong> go to the club's administrators once per level per period: <em>upcoming invoice</em> (before the period starts), <em>payment due</em>, and <em>payment overdue</em> (the last 7 days of grace and
                        after). The last one says what happens next: moved to the free plan, and archived if auto-archive is on.
                    </li>
                    <li>Zero-amount periods (free plans, trials) never produce reminders.</li>
                </ul>
            </div>
        </section>

        <section id="glossary" class="card scroll-mt-4">
            <div class="px-4 py-3 pb-1"><h2 class="card-title">Glossary</h2></div>
            <div class="overflow-x-auto">
                <table class="table">
                    <tbody>
                        {#each [
                            ['Period', 'One billing term for a club (called a “due” in the data): price, dates, invoice and payments.'],
                            ['Cycle', 'The repeating sequence of periods for one club.'],
                            ['Invoice lead', 'How many days before a period starts it is opened and invoiced (set per plan).'],
                            ['Grace', 'Days to pay after the period start (or the invoice date, if later) before the period is overdue.'],
                            ['Overdue', 'Unpaid or partly paid and past its grace.'],
                            ['Downgraded', 'On the free plan because of an overdue period. Reverses when it is settled.'],
                            ['Archived', 'Subdomain off, data kept. Restorable.'],
                            ['Lapsed', 'The last period has ended and no new one was opened (for example auto-renew off).'],
                            ['Net / total', 'Net is the plan price; total is net plus VAT. Balances use the total.'],
                            ['Reverse charge', 'Cross-border EU business sale: 0% VAT with a note; the customer accounts for the VAT.'],
                            ['Credit note', 'A document that reduces an invoice (for example the unused part after a plan change). Own sequence CN-YYYY-nnnnn; never edited.'],
                            ['Club credit', 'Money the club has paid or been credited that is not yet used. Applied automatically to new invoices, carried forward, never refunded by the system.'],
                            ['Prorated', 'A period that was shortened by a plan change and priced by the days actually used.'],
                            ['Cancelled period', 'An advance-invoiced period dropped by a plan change. Its invoice is kept and credited in full; nothing is owed for it.'],
                            ['Voided invoice', 'An invoice that was replaced because it was wrong. It keeps its number and PDF (marked VOID) and is never reused.'],
                            ['Void and reissue', 'The correction action: voids the invoice, issues a full credit note (documents only) and a replacement invoice for the same period. Payments and balances do not change.'],
                            ['Number pattern', 'The format of automatic invoice or credit note numbers, with placeholders such as {YYYY} and {NNNNN}. Set on the Invoicing tab.'],
                            ['Counter', 'The running sequence behind automatic numbers. It restarts every year, every month or never, depending on the date placeholders in the pattern, and only moves forward.'],
                            ['Snapshot', 'The frozen copy of issuer, club and amounts stored on an invoice when it is issued.']
                        ] as [term, meaning] (term)}
                            <tr>
                                <td class="w-48 align-top font-semibold text-ink">{term}</td>
                                <td>{meaning}</td>
                            </tr>
                        {/each}
                    </tbody>
                </table>
            </div>
        </section>
    </article>
</div>
