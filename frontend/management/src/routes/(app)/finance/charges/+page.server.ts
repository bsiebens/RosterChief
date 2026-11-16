import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/** The list of charge invoices is part of the central invoices screen. */
export const load: PageServerLoad = () => redirect(303, '/finance/invoices?type=charge');
