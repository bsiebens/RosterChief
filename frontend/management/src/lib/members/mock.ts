/** Placeholder data for the member profile's evaluations and documents. Replace with API data later:
 *  the components only depend on these shapes. */

export type Evaluation = {
	id: string;
	date: string;
	season: string;
	evaluator: string;
	team: string;
	overall: number;
	scores: { key: 'technique' | 'tactics' | 'fitness' | 'attitude' | 'teamwork'; value: number }[];
	comment: string;
};

export type DocStatus = 'signed' | 'pending' | 'expired' | 'missing';
export type MemberDocument = { key: 'consent' | 'medical' | 'photo' | 'id' | 'conduct' | 'transfer'; status: DocStatus; date?: string; due?: string };

export const mockEvaluations = (): Evaluation[] => [
	{
		id: 'e3',
		date: '2026-10-12',
		season: "'26 - '27",
		evaluator: 'Sam Verhoeven',
		team: 'U16',
		overall: 4.2,
		scores: [
			{ key: 'technique', value: 4 },
			{ key: 'tactics', value: 4 },
			{ key: 'fitness', value: 5 },
			{ key: 'attitude', value: 4.5 },
			{ key: 'teamwork', value: 4 }
		],
		comment: 'Strong start of the season. Reads the game well and leads the warm-ups. Keep working on finishing under pressure.'
	},
	{
		id: 'e2',
		date: '2026-03-02',
		season: "'25 - '26",
		evaluator: 'Sam Verhoeven',
		team: 'U16',
		overall: 3.8,
		scores: [
			{ key: 'technique', value: 3.5 },
			{ key: 'tactics', value: 3.5 },
			{ key: 'fitness', value: 4.5 },
			{ key: 'attitude', value: 4 },
			{ key: 'teamwork', value: 3.5 }
		],
		comment: 'Good progress since the autumn. Fitness is a real asset; positioning still needs attention.'
	},
	{
		id: 'e1',
		date: '2025-10-06',
		season: "'25 - '26",
		evaluator: 'Lena Maes',
		team: 'U14',
		overall: 3.2,
		scores: [
			{ key: 'technique', value: 3 },
			{ key: 'tactics', value: 3 },
			{ key: 'fitness', value: 3.5 },
			{ key: 'attitude', value: 4 },
			{ key: 'teamwork', value: 3 }
		],
		comment: 'Enthusiastic and coachable. Building the basics.'
	}
];

export const mockDocuments = (): MemberDocument[] => [
	{ key: 'consent', status: 'signed', date: '2026-08-21' },
	{ key: 'medical', status: 'signed', date: '2026-08-21' },
	{ key: 'photo', status: 'signed', date: '2026-08-22' },
	{ key: 'id', status: 'pending', due: '2026-11-30' },
	{ key: 'conduct', status: 'expired', date: '2025-08-20', due: '2026-08-20' },
	{ key: 'transfer', status: 'missing', due: '2026-12-15' }
];

export const documentsDone = (docs: MemberDocument[]) => docs.filter((d) => d.status === 'signed').length;
