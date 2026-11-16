import type { AuthState } from '#lib/types.ts';

declare global {
	namespace App {
		interface Locals {
			auth: AuthState;
		}
	}
}

export {};
