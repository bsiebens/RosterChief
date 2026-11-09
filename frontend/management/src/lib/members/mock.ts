/** Placeholder data for the member profile's documents. Replace with API data later:
 *  the components only depend on these shapes. */

export type DocStatus = 'signed' | 'pending' | 'expired' | 'missing';
export type MemberDocument = { key: 'consent' | 'medical' | 'photo' | 'id' | 'conduct' | 'transfer'; status: DocStatus; date?: string; due?: string };

export const mockDocuments = (): MemberDocument[] => [
	{ key: 'consent', status: 'signed', date: '2026-08-21' },
	{ key: 'medical', status: 'signed', date: '2026-08-21' },
	{ key: 'photo', status: 'signed', date: '2026-08-22' },
	{ key: 'id', status: 'pending', due: '2026-11-30' },
	{ key: 'conduct', status: 'expired', date: '2025-08-20', due: '2026-08-20' },
	{ key: 'transfer', status: 'missing', due: '2026-12-15' }
];

export const documentsDone = (docs: MemberDocument[]) => docs.filter((d) => d.status === 'signed').length;
