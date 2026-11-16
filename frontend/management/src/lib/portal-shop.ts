import type { OrderingWindow } from '#lib/shop.ts';

export type ShopProduct = {
	id: string;
	name: string;
	category: string;
	description: string;
	image_url: string | null;
	price: string;
	in_stock: boolean;
	next_ordering_window: OrderingWindow | null;
	personalization_enabled: boolean;
	variants: { id: string; name: string; effective_price: string; in_stock: boolean }[];
	files: { title: string; url: string }[];
};

export type ShopCatalog = {
	viewer_id: string;
	people: { id: string; name: string }[];
	open: boolean;
	closed_message: string;
	payment_instructions: string;
	products: ShopProduct[];
};

export type CartLine = {
	id: string;
	name: string;
	variant: string;
	product_id: string;
	variant_id: string | null;
	beneficiary_id: string | null;
	quantity: number;
	unit_price: string;
	ordering_deadline: string | null;
	estimated_delivery: string | null;
	personalization_name: string;
	personalization_number: string;
};

export type ShopReceipt = {
	id: string;
	number: string;
	created: string;
	fulfillment_status: string;
	payment_status: string;
	pickup_instructions: string;
	invoice_number: string | null;
	lines: { id: string; name: string; variant: string; quantity: number; amount: string }[];
	discounts: { description: string; amount: string }[];
	total: string;
	due: string;
};

export type OrderHistory = { results: ShopReceipt[]; more: boolean };
