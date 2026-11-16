# RosterChief club management (SvelteKit)

The club-facing management panel: members and (later) teams, calendar, finance. Svelte 5 runes, SvelteKit, Tailwind 4,
`adapter-node`. It never talks to Django from the browser: this app proxies `/_allauth/app/v1/`, `/api/v1/`, `/media/`
and private member photos to the backend and keeps the allauth session token in an httpOnly cookie (`rc_mgmt_session`).

## Run

```sh
npm i
cp .env.example .env
npm run dev        # port 5174
```

- Backend on `:8000` with a club `acme` provisioned.
- A club: <http://acme.localhost:5174> (browsers resolve `*.localhost` to 127.0.0.1).
- The bare domain / club picker: <http://localhost:5174>.

`npm run check` type-checks, `npm run build` makes the Node server (`node build`).

## How hostnames work

`hooks.server.ts` asks the public `GET /api/v1/club` for the request's hostname (cached ~60 s per host):

| Answer | Mode | What you get |
| --- | --- | --- |
| `is_platform: true` | `platform` | Generic RosterChief sign-in. After sign-in: 0 clubs -> "no clubs" page, 1 club -> redirect to its host, 2+ -> picker at `/`. |
| `is_platform: false` | `club` | Club-branded sign-in and the panel shell (sidebar, topbar). |
| 404 / backend down | `error` | Friendly `/club-not-found` page (404 or 503). |

The club's colours become CSS variables on `<html style>` (`--tenant-club`, `--tenant-sidebar-bg`, ...; only `#RRGGBB`
is accepted) and `routes/layout.css` maps them to the `club` / `sidebar-*` colour tokens. Page titles are
`{page} · {club name}`.

Club URLs in the picker use the current request's scheme and port plus the `host` from `/api/v1/my-clubs`
(behind a reverse proxy set `ORIGIN` / `PROTOCOL_HEADER` / `HOST_HEADER` for adapter-node so `event.url` is the public URL).

## Session cookie and domains

The cookie is host-scoped unless told otherwise:

- Production: set `ROSTERCHIEF_BASE_DOMAIN=rosterchief.app`. The cookie then gets `Domain=.rosterchief.app` (only on
  hosts under that domain), so a login on the bare domain also works on `acme.rosterchief.app`.
- Development (`ROSTERCHIEF_BASE_DOMAIN=localhost`): browsers do not share cookies between `localhost` and
  `acme.localhost`, so the cookie stays host-only and the picker redirect to a club **asks you to sign in again** on
  the club host. That is expected.

The `rc_lang` cookie (en/nl/fr) follows the same rule. `?lang=nl` on any URL also sets it.

## Layout data contract

Every page: `data.auth`, `data.lang`, `data.mode` (`'platform' | 'club' | 'error'`), `data.branding` (club mode).
Pages under `(app)`: also `data.me` (the `/api/v1/me` payload; only null on the picker, `/no-access` and the account screens
during MFA enrolment) and `data.clubs` (platform mode: the user's clubs with absolute `url`).
Top-bar content comes from `usePageHeader(() => ({ title, context, actions }))` in `lib/header.svelte.ts`.

## Messages (i18n)

Add `src/lib/messages/<namespace>.ts` exporting `{ en: {...}, nl: {...}, fr: {...} }` (keys like `members.title`);
every file there is picked up automatically. Use `t('key', { name })` with `{name}` placeholders. Missing keys fall
back to English, then show the key itself.

## Adding a nav item

Edit the `NAV` array in `src/lib/nav.ts`: `key`, `href`, `label` (an i18n key), a lucide `icon`, an optional
`visible: (me) => can(me, 'app.permission')` (or `me.is_club_admin`) and optional `children` (sub-items, shown only
while the section is active). Sections that are not built yet (Teams, Calendar, Finance, Settings) are intentionally
absent.
