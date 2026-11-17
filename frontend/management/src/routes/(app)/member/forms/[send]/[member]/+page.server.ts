import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/** Old direct links (notifications, emails): the form now opens in a drawer on the Forms page. */
export const load: PageServerLoad = ({ params }) => redirect(303, `/member/forms?fill=${encodeURIComponent(params.send)}.${encodeURIComponent(params.member)}`);
