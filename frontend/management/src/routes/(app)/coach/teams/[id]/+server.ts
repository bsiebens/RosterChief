import { redirect } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

/** The roster lives on the Squad tab now; old links to a team page land there. */
export const GET: RequestHandler = ({ params }) => redirect(307, `/coach?tab=teams&team=${params.id}`);
