export type PublicFieldType = 'text' | 'textarea' | 'number' | 'email' | 'date' | 'choice' | 'multichoice' | 'checkbox' | 'file';
export type PublicField = { id: string; key: string; label: string; type: PublicFieldType; required: boolean; help_text: string; section: string; options: { value: string; label: string }[] };
export type PublicFormStatus = 'open' | 'closed' | 'not_open_yet' | 'inactive';
export type PublicForm = {
	title: string;
	description: string;
	state: { status: PublicFormStatus; opens_at: string | null; closes_at: string | null };
	fields: PublicField[];
	max_file_mb: number;
	allowed_file_extensions: string[];
};

