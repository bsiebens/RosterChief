import type { AuthState, Branding, HostMode, MyClub } from '#lib/types.ts';
import type { Lang } from '#lib/i18n.svelte.ts';
import type { FixturePreview } from '#lib/fixtures.ts';

declare global {
	namespace App {
		interface Locals {
			auth: AuthState;
			lang: Lang;
			/** 'platform' = bare domain (club picker), 'club' = a club's own host, 'error' = unknown club / backend down. */
			mode: HostMode;
			/** Public branding of the club behind this hostname (club mode only). */
			branding: Branding | null;
			/** Clubs the signed-in user belongs to (platform mode only). */
			myClubs: MyClub[];
			/** Why mode is 'error'. */
			hostError: 'not_found' | 'unreachable' | null;
		}
		interface PageState {
			/** A re-check's preview handed from the team page to the fixture import wizard (never stored anywhere else). */
			fixturePreview?: FixturePreview;
		}
	}
}

export {};
