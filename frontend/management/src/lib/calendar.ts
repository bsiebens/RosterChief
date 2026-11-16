export type Location = {
	id: string;
	name: string;
	address: string;
	city: string;
	zip_code: string;
	country: string;
	country_name: string;
	is_home: boolean;
	latitude: number | null;
	longitude: number | null;
	event_count: number;
};
export type Opponent = { id: string; name: string; logo_url: string | null; alternative_names: string[]; event_count: number };
export type Country = { code: string; name: string };
