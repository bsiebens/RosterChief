import { api } from '#lib/management-api.ts';
import { asList, type PagedList, type StockRow } from '#lib/shop.ts';

type Fetch = typeof fetch;

/** Every inventory-managed stock line (the API pages at 200). */
export async function loadAllStock(f: Fetch): Promise<{ rows: StockRow[]; error: number | null }> {
	const rows: StockRow[] = [];
	for (let offset = 0; ; offset += 200) {
		const res = await api<PagedList<StockRow> | StockRow[]>('GET', '/shop/inventory', { query: { limit: 200, offset: offset || undefined }, f });
		if (!res.ok) return { rows, error: res.status };
		const list = asList(res.data);
		rows.push(...list.results);
		if (rows.length >= list.count || !list.results.length) return { rows, error: null };
	}
}
