<script lang="ts">
	// Static explainer: how billing works. Keep it in step with billing/services (dues.py, reminders.py) and the beat
	// schedule in rosterchief/celery.py; the job times below are the code defaults and can be changed on the Jobs page.
	const SECTIONS = [
		['cycles', 'Billing cycles'],
		['timeline', 'From invoice to archive'],
		['renewal', 'Auto-renew and lapsed clubs'],
		['trials', 'Trials'],
		['unpaid', 'Unpaid periods'],
		['vat', 'VAT and reverse charge'],
		['invoices', 'Invoices and numbers'],
		['payments', 'Payments, waiving and reminders'],
		['glossary', 'Glossary']
	] as const;
</script>

<svelte:head><title>How billing works · RosterChief</title></svelte:head>

<div class="grid items-start gap-6 xl:grid-cols-[14rem_1fr]">
	<nav class="card sticky top-0 hidden p-3 xl:block" aria-label="On this page">
		<div class="mb-2 font-mono text-[10px] tracking-[.08em] text-muted uppercase">On this page</div>
		<ul class="flex flex-col gap-0.5 text-sm">
			{#each SECTIONS as [id, label] (id)}<li><a class="block rounded px-2 py-1 text-slate hover:bg-rule" href="#{id}">{label}</a></li>{/each}
		</ul>
	</nav>

	<article class="flex max-w-4xl flex-col gap-4 text-sm leading-relaxed text-slate">
		<div class="card"><div class="card-body gap-2">
			<p>
				RosterChief invoices its clubs. A club is on a <strong>plan</strong>, which decides what it can use. When a club is <strong>billed</strong>, it also has a stream of <strong>periods</strong>: each one has a price, an invoice, a deadline and payments. This page explains how those parts fit together. Everything here is automatic; the scheduled jobs do the work, and you step in for exceptions.
			</p>
		</div></div>

		<section id="cycles" class="card scroll-mt-4"><div class="card-body gap-3">
			<h2 class="card-title">Billing cycles</h2>
			<p>Every club has its <strong>own cycle</strong>, anchored on the day it was subscribed (an “anniversary” cycle). A period starts that day and lasts the plan's <em>period length</em> (for example 12 months). The next period starts the day after the previous one ends, so there are never gaps or overlaps.</p>
			<ul class="list-disc space-y-1 pl-5">
				<li><strong>Price and VAT are fixed when a period opens.</strong> Changing a plan's price later (prices are dated, a new price is a new row) only affects periods opened after the new price starts.</li>
				<li><strong>Prices exclude VAT.</strong> VAT is added on top, see below.</li>
				<li><strong>No proration.</strong> Changing a club's plan changes what it can use straight away, but the price changes from the next period.</li>
				<li>Each plan has three timing settings (Plans &amp; pricing): <em>period length</em>, <em>invoice N days before the period starts</em> and <em>grace days</em>.</li>
			</ul>
		</div></section>

		<section id="timeline" class="card scroll-mt-4"><div class="card-body gap-4">
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
		</div></section>

		<section id="renewal" class="card scroll-mt-4"><div class="card-body gap-3">
			<h2 class="card-title">Auto-renew and lapsed clubs</h2>
			<ul class="list-disc space-y-1 pl-5">
				<li><strong>Auto-renew on</strong> (default): the next period is opened and invoiced <em>in advance</em>, once the current one ends within the plan's invoice lead. A club never goes off its plan between periods.</li>
				<li><strong>Auto-renew off</strong>: nothing is opened in advance, so the club's period will end. Once it has ended, the next period is opened anyway (“lapsed”), starting the day after the old one ended, so the gap is billed. Its grace is counted from the day it was invoiced, so the club still has the full grace days to pay. From there the normal overdue steps apply.</li>
				<li>“Open next period now” on the Cycles tab does the same thing early by hand.</li>
				<li>If the renewal job itself fails, tomorrow's run picks the club up again; failures appear on the Jobs page and in the dashboard alerts.</li>
			</ul>
		</div></section>

		<section id="trials" class="card scroll-mt-4"><div class="card-body gap-3">
			<h2 class="card-title">Trials</h2>
			<p>A trial belongs to a <strong>club</strong>, not to a plan: any plan can be trialled. Tick “Start with a free trial” when you start billing for a club and choose the number of days (default 30).</p>
			<ul class="list-disc space-y-1 pl-5">
				<li>The club gets the chosen plan's features <strong>at once</strong>, for free, for the number of days chosen. The trial is a period of its own (marked “trial”, zero amount, nothing to pay).</li>
				<li>The first <strong>paid</strong> period starts the day after the trial ends and is invoiced automatically in advance, like any renewal. After the trial the club stays on the <strong>same plan</strong>; use “Change plan” on the club page if it should move to another one.</li>
				<li>The club keeps its plan's features through the whole changeover. It is not moved to the free plan when the trial ends, and it does not get a different plan early.</li>
				<li>A trial can only be given when billing starts. For a club that is already billed, use a per-club override on the club's Features card instead (a feature granted for a limited time).</li>
			</ul>
		</div></section>

		<section id="unpaid" class="card scroll-mt-4"><div class="card-body gap-3">
			<h2 class="card-title">Unpaid periods: downgrade first, archive later</h2>
			<ul class="list-disc space-y-1 pl-5">
				<li><strong>Downgrading</strong> always happens once a period is overdue. It is reversible and keeps the club usable on the free plan's limits.</li>
				<li><strong>Archiving</strong> happens only if the club's <em>auto-archive</em> is on, and only after the “days between downgrade and archive” setting (Invoicing tab; 0 = archive right after downgrading).</li>
				<li><strong>Where is auto-archive set?</strong> Per club: chosen when you start billing (default on) and changeable on the club page, Billing card, “Archive when overdue”. Turn it off for clubs that should never be archived automatically; they are still downgraded.</li>
				<li>An archived club can be restored from the club page. Opening its next period (or reactivating it) restores it and bills the time it was away.</li>
			</ul>
		</div></section>

		<section id="vat" class="card scroll-mt-4"><div class="card-body gap-3">
			<h2 class="card-title">VAT and reverse charge</h2>
			<ul class="list-disc space-y-1 pl-5">
				<li>The <strong>standard rate</strong> is set on the Invoicing tab (default 21%) and added on top of the plan price: 50.00 + 21% = 60.50 payable.</li>
				<li><strong>Reverse charge</strong> (0% VAT, noted on the invoice) applies when the club has a VAT number <em>and</em> its legal country is an EU member state different from the issuer's country. It needs the issuer's country on the Invoicing tab; the readiness checklist there warns when it is missing.</li>
				<li>Free and trial periods carry no VAT.</li>
				<li>The rate and amounts are <strong>frozen on the period</strong> when it opens. Later changes to the rate do not touch existing periods or invoices.</li>
				<li>Everything that is “owed” or “paid” works on the <strong>total including VAT</strong>.</li>
			</ul>
		</div></section>

		<section id="invoices" class="card scroll-mt-4"><div class="card-body gap-3">
			<h2 class="card-title">Invoices and numbers</h2>
			<ul class="list-disc space-y-1 pl-5">
				<li>An invoice is created automatically when a period opens. It stores a <strong>snapshot</strong> of the issuer details, the club's details and the amounts, so editing settings or the club later never changes an invoice that has been issued. Payments received are shown live.</li>
				<li><strong>Numbers</strong> are automatic and gapless per year: <span class="font-mono">INV-2026-00001</span>, <span class="font-mono">INV-2026-00002</span>, … A number is only taken when an invoice is successfully created, so failures never leave holes.</li>
				<li><strong>Custom numbers</strong> exist for invoices issued <em>outside</em> the system: when issuing an invoice by hand for a period that has none, you can give your own number. Custom numbers never touch the automatic sequence, cannot look like <span class="font-mono">INV-YYYY-nnnnn</span> and cannot be changed afterwards. Numbers are never editable once an invoice exists.</li>
				<li><strong>Sending:</strong> “Send by email” mails the PDF to the club's administrators. “Mark as sent” records that you sent it yourself.</li>
				<li>The PDF shows the issuer, the club, net / VAT / total, the due date, and payment details with the invoice number as the payment reference.</li>
			</ul>
		</div></section>

		<section id="payments" class="card scroll-mt-4"><div class="card-body gap-3">
			<h2 class="card-title">Payments, waiving and reminders</h2>
			<ul class="list-disc space-y-1 pl-5">
				<li><strong>Record payment</strong> adds a payment (a partial payment is fine). A period is paid when payments reach the total; a payment larger than the open balance is refused. Removing a payment re-settles the period.</li>
				<li><strong>Waive</strong> forgives an unpaid period that has no payments. It is for exceptions and cannot be undone.</li>
				<li><strong>Reminders</strong> go to the club's administrators once per level per period: <em>upcoming invoice</em> (before the period starts), <em>payment due</em>, and <em>payment overdue</em> (the last 7 days of grace and after). The last one says what happens next: moved to the free plan, and archived if auto-archive is on.</li>
				<li>Zero-amount periods (free plans, trials) never produce reminders.</li>
			</ul>
		</div></section>

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
							['Snapshot', 'The frozen copy of issuer, club and amounts stored on an invoice when it is issued.']
						] as [term, meaning] (term)}
							<tr><td class="w-48 align-top font-semibold text-ink">{term}</td><td>{meaning}</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	</article>
</div>
