<script lang="ts">
	import { CalendarDays, IdCard, ShoppingCart, Shirt, Users } from '@lucide/svelte';
	import { t } from '#lib/i18n.svelte.ts';
	import type { Overview } from '#lib/overview.ts';
	import type { ShopStats } from '#lib/shop-orders.ts';

	/** The five summary cards along the bottom. The Shop card shows GET /shop/stats (dashes when it failed), and is only shown when the club has the shop feature and the user may manage it. */
	let { cards, showShop = false, shop = null }: { cards: NonNullable<Overview['cards']>; showShop?: boolean; shop?: ShopStats | null } = $props();
	const money = (v: string) => `€${Number(v).toFixed(2)}`;
	const dash = '—';
	const groups = $derived([
		{ title: t('overview.card.people'), icon: Users, rows: [['overview.row.admins', cards.people.admins], ['overview.row.staff', cards.people.staff], ['overview.row.members', cards.people.members], ['overview.row.guardians', cards.people.guardians]] as [string, string | number][] },
		{ title: t('overview.card.members'), icon: IdCard, rows: [['overview.row.total', cards.members.total], ['overview.row.active', cards.members.active], ['overview.row.pending', cards.members.pending], ['overview.row.lapsed', cards.members.lapsed]] as [string, string | number][] },
		{ title: t('overview.card.teams'), icon: Shirt, rows: [['overview.row.teams', cards.teams.teams], ['overview.row.players', cards.teams.players], ['overview.row.staff_season', cards.teams.staff]] as [string, string | number][] },
		{ title: t('overview.card.events'), icon: CalendarDays, rows: [['overview.row.upcoming', cards.events.upcoming], ['overview.row.this_season', cards.events.this_season]] as [string, string | number][] },
		...(showShop
			? [{ title: t('overview.card.shop'), icon: ShoppingCart, rows: [['overview.row.orders', shop?.orders_total ?? dash], ['overview.row.revenue', shop ? money(shop.revenue) : dash], ['overview.row.outstanding', shop ? money(shop.outstanding) : dash], ['overview.row.orders_unpaid', shop?.unpaid_count ?? dash]] as [string, string | number][] }]
			: [])
	]);
</script>

<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
	{#each groups as g (g.title)}
		<div class="card card-body gap-1">
			<h2 class="mb-1 flex items-center gap-2 font-display text-sm font-extrabold tracking-[.06em] text-ink uppercase"><g.icon size={16} aria-hidden="true" /> {g.title}</h2>
			{#each g.rows as [key, value] (key)}
				<div class="flex items-center justify-between gap-3 py-1.5 text-sm"><span class="text-muted">{t(key)}</span><span class="font-mono text-sm font-semibold text-ink">{value}</span></div>
			{/each}
		</div>
	{/each}
</div>
