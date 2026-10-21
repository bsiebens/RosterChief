# DECOUPLING-ADMIN.md — Moving `management/` and `controlpanel/` off server rendering (design)

Status: **proposal, no code changed.** Branch `claude/architecture-decoupling-research`, 2026-10-21.
Follow-up to `DECOUPLING.md`, which recommended keeping these two surfaces server-rendered. This
document answers "what if we don't?" and says when that becomes the right call.

The schema constraint from `DECOUPLING.md` §6 applies unchanged: **no schema change without an
expand → migrate → contract path.** Nothing here needs one; an admin API is an adapter over the
existing models and services.

## 1. TL;DR

- It is **feasible and the codebase is better placed for it than it looks**, because `appapi/`
  already proves the pattern that makes it affordable: bind JSON to the *existing Django forms and
  formsets* (`MemberProfileForm`, `RegistrationEntryFormSet`) so validation and business rules stay
  in one place.
- It is **not cheap**: 293 routes (236 `management`, 57 `controlpanel`), ≈12k lines of view/form
  code to re-express, and 16k+ lines of tests that today assert HTML and would have to be rewritten
  as API tests plus browser tests.
- **Recommendation: do it as a strangler, not a rewrite**, and only if one of the triggers in §7
  holds. Start with `controlpanel/` plus one high-interaction `management` area. Never switch both
  surfaces off at once. If none of the triggers holds, stay with `DECOUPLING.md`.

## 2. What would actually be moved (measured 2026-10-21)

| | `management/` (club admin) | `controlpanel/` (platform operators) |
|---|---|---|
| URL patterns | 236 | 57 |
| View classes/functions | 260 | 58 |
| Production code | `views.py` 8,067 lines, `forms.py` 2,170 lines (81 form classes) | `views.py` 1,373 lines, 14 form classes |
| Templates | 119 | 29 |
| Tests (HTML-asserting) | 16,320 lines, 1,316 tests | 2,771 lines, 266 tests |
| Permission mixin references | 271 | not measured |
| Formset usages | 79 | – |
| `notify()` flash calls | 280 | 70 |
| File exports | PDF (WeasyPrint), XLSX (openpyxl), CSV, bulk import | – |
| Audience | club staff of every club (many roles) | a handful of platform staff |

What each of those turns into:

| Server-rendered thing | In a decoupled admin |
|---|---|
| URL + view | An endpoint (list / detail / create / update / action) |
| Django `Form` / `ModelForm` | Keep as the validation engine; expose its **field schema** and **errors** as JSON (§4.2) |
| Formset (79) | Nested array in the payload; same `FormSet` validates it |
| `*RequiredMixin` (permissions) | A ninja dependency calling the same `club.services.access` function |
| `notify(request, "level\|title\|body")` flash | Response field (`messages: [...]`) or toast from a status code; titles/bodies stay translated server-side |
| htmx partial swap | Client state update from the mutation response |
| PDF / XLSX responses | Authenticated download endpoints, unchanged generators |
| Pagination, sort, filter in query string | Same query params, JSON envelope with cursor/page |
| `{% trans %}` in templates | Needs a front-end i18n story (§4.5) |

## 3. Options

| Option | Description | Cost | Risk | Verdict |
|---|---|---|---|---|
| **0. Stay** | Server-rendered + htmx + islands (`DECOUPLING.md`) | low | none | default |
| **1. Strangler** | New SPA mounted beside the legacy pages; screens move one at a time; both share the session and navigation | medium, spread out | low per step | **recommended if a trigger holds** |
| **2. Big bang** | Rewrite all 293 routes, delete templates | very high | high: long-lived branch, all roles at once, no revert | **not recommended** |
| **3. Headless-admin frameworks** (Refine, react-admin, Django Unfold) | Generic CRUD UI over an API | medium | per-club RBAC and tenancy are bespoke; generic CRUD fits maybe the table/list screens only | borrow ideas, not the framework |

## 4. Design for Option 1

### 4.1 Surface and auth

- New API at **`/api/manage/v1/`** (a third `NinjaAPI`, own OpenAPI, own error envelope reusing
  `appapi/errors.py`). Do **not** add it to `/api/app/v1/` (token, mobile-oriented, no cookies,
  `API.md` §2) or `/api/v1/` (public, wildcard CORS).
- **Same-origin session cookie + CSRF**, not bearer tokens. The SPA is served from the club's own
  host (`<club>.rosterchief.app/manage/…`), so the existing login, MFA (`RequireMFAMiddleware`),
  maintenance mode and tenant middleware apply unchanged, and there is no token in JS-reachable
  storage. No CORS.
- Platform operators (`controlpanel`) use the base/admin host (`rosterchief/admin_host.py`), same mechanism.
- Static SPA only (Vite build, served by Caddy/WhiteNoise). **No Node SSR server**: the admin is
  behind login, so SEO and server rendering don't matter, and one more runtime would break the
  "one image, one process type" deployment.

### 4.2 Forms without duplicating rules (the cost-saver)

Hand-rewriting 95 forms as Pydantic schemas would fork validation. Instead:

1. **`GET /api/manage/v1/forms/{form_key}/schema`** returns field name, type, label, help text,
   required, choices (tenant-scoped querysets evaluated server-side), initial values and widget hints,
   introspected from the Django form class (labels are already `gettext_lazy`, so they come out
   translated for the request language).
2. **`POST`/`PATCH`** binds the JSON to that same form class and returns `422` with
   `{fields: {name: [errors]}, non_field: [...]}` on failure.
3. The SPA renders from the schema with a generic field renderer and overrides it for the few
   bespoke screens.

Consequences: `clean()` logic (for example jersey-number uniqueness, season scoping) stays in Python;
adding a form field needs **no** frontend change for generic screens; the cost moves from "rewrite
rules" to "build one good generic form renderer". Limit: dynamic widgets (searchable selects with
remote options, dependent dropdowns) need a small `options` endpoint contract; today's
`static/js/searchable-select.js` is the model.

### 4.3 Endpoint conventions

- Resource shape: `GET /{resource}` (filter, sort, cursor/page), `GET /{resource}/{id}`,
  `POST`, `PATCH`, `DELETE`, plus verb sub-resources for actions (`POST /events/{id}/cancel`).
  Reuse `appapi.pagination.paginate` and `appapi.idempotency.idempotent` for writes that can double-fire (payments, invoices).
- **Permissions in the service layer first.** Each former `*RequiredMixin` becomes one dependency
  calling the same `club.services.access` function. Add a generated **route × role matrix test**
  (every route against every club role and a foreign-club user must be 403/404 as expected); that
  replaces the implicit coverage the HTML view tests give today.
- **View-models via presenters**, not model serialisation, so the wire shape is decoupled from the
  schema (this is what lets tables change under the API without a `v2`).
- Exports: keep generators in `management/pdf.py`, `shop_export.py`, `spreadsheet.py`; endpoints
  stream `FileResponse`. Long PDFs (WeasyPrint) should return `202` + a poll URL once any takes >~5 s
  (**UNVERIFIED**: measure first).
- Bulk import (`bulk_import.py`): upload → server-side dry-run plan → confirm; already shaped like that (`_event_import_plan.html`).

### 4.4 Front end

- **React + TypeScript + Vite**, TanStack Router/Query, client generated from the `manage` OpenAPI
  (same `openapi-typescript` pipeline as in `DECOUPLING.md` §5.1). Rationale: the member app
  (Expo/React Native) shares types, API hooks, validation helpers and design tokens with it, so one
  team covers both. Svelte or Vue are equally able; choose them only if nobody on the team will
  also touch the React Native app. (This is a team-fit judgement, not a benchmark.)
- Component layer: a headless set (Radix-based, e.g. shadcn/ui) over **Tailwind 4**, which the repo
  already uses. daisyUI is class-based CSS and would not carry over to components cleanly (**UNVERIFIED**
  which parts would be reused).
- Tests: Vitest for logic, **Playwright** for end-to-end (Chromium is already provisioned in these
  environments), run against the Django test server with `seed_demo_data`.

### 4.5 Internationalisation

Only `marketing` has translations today, but the codebase is kept translation-ready. Server-built
strings (form labels, errors, `notify()` text, enum labels) arrive translated through
`Accept-Language`, which is the cheapest path and keeps `makemessages` as the single catalogue.
Static UI chrome in the SPA needs its own catalogue (i18next or lingui) with the extraction step in CI.
Do not duplicate server strings in the SPA.

### 4.6 Coexistence (how the strangler works)

- Host the SPA at `/manage/app/*` (or a flag per route using the existing `waffle` flags). Legacy
  pages and the SPA share the **session cookie and navigation shell**, so a user clicks between a
  new screen and an old one without noticing a boundary; legacy templates link to SPA routes and back.
- A screen is "done" when its SPA version passes the route × role matrix and its Playwright flow, the
  legacy URL redirects to it, and its legacy view, template and HTML tests are **deleted in the same
  PR**. No long-term double maintenance.
- Deployment skew: SPA and API ship in one image, so they deploy atomically. Stale bundles in an
  open browser tab are handled by an `X-Build-Id` response header: the client reloads when it
  changes, and `manage/v1` stays additive (same rule as `API.md` §2).

## 5. Gains and costs, plainly

**Gains**
- Rich interaction without hand-written JS per screen (drag-and-drop lineup, inline editing,
  live-updating dashboards, optimistic UI, keyboard-driven bulk entry).
- One TypeScript stack and one generated contract across admin and the native app.
- Management functions become scriptable and integratable (partners, exports, automation) through a documented API instead of screen scraping.
- Front-end and back-end work can proceed in parallel; Python reviewers no longer review HTML.
- Real component-level and browser tests.

**Costs**
- Rewriting ≈12k lines of view/form code and replacing 19k lines of tests (they assert HTML and
  can't be ported mechanically; most business assertions can be re-expressed against services or the API).
- Two build/test toolchains in CI and for contributors; node is already needed for Tailwind, but a
  SPA adds a dependency tree to audit (`pip-audit` equivalent: `npm audit`).
- Everything the server gave for free must be re-done on the client: loading/error/empty states,
  form dirty tracking, permission-aware nav, accessibility, i18n of chrome, back/forward behaviour.
- Permission regressions are the main danger: with SSR every page was gated by a mixin; with an API
  a forgotten dependency is an exposed endpoint. The matrix test (§4.3) is mandatory, not optional.
- Rough effort: **order of 150–300 developer-days** for the full 293 routes at 0.5–1 day per route
  including tests. This is an extrapolation from route count, not a measurement; measure by
  migrating 10 representative routes first and re-estimate.

## 6. What stays the same

Tenancy, RBAC, services, ORM, migrations, i18n catalogues, PDF/XLSX generation, scheduler, push,
public site (`marketing/`), the public API and the member app API. Legacy pages keep working during
the whole migration.

## 7. When is it worth it? (triggers)

Go to Option 1 if **at least two** hold; otherwise stay with Option 0:

1. The native app is happening on React Native/TypeScript, so a React admin shares most of the toolchain.
2. Specific admin screens are repeatedly blocked by interactivity limits (lineup, schedule editing, bulk grids, live dashboards).
3. Clubs or partners ask for integrations that need a management API.
4. Reviewer/merge friction from the 8k-line `views.py` and 16k-line `tests.py` persists after the cheap
   refactors in `DECOUPLING.md` §5.4 (splitting modules).
5. A front-end specialist is available to own it.

If only trigger 2 holds, use islands (`DECOUPLING.md` §5.3), not a SPA.

## 8. Phasing

| Phase | Scope | Exit criteria |
|---|---|---|
| **A — Foundations** | `manage` NinjaAPI, session+CSRF auth dependency, error envelope, form-schema introspection, route × role matrix harness, SPA shell served at `/manage/app/`, shared nav | one read-only screen live behind a flag, matrix test green |
| **B — Pilot: `controlpanel/`** | The 57 routes, internal users only (lowest blast radius) | platform staff use only the SPA for 2 weeks; legacy views deleted |
| **C — Pilot: one `management` area** | Choose the most interactive area (events/lineup or registrations) | re-estimate remaining effort from real throughput |
| **D — Go/no-go** | Compare actual velocity with §5 estimate | written decision to continue, pause or stop |
| **E — Remaining areas** | Move by domain: members → teams → news → shop → billing → formbuilder → evaluations → settings | each area removes its templates and HTML tests |
| **F — Retire** | Delete unused templates, `management` Tailwind bundle, htmx vendoring if no longer used | build is smaller, `npm run build` shrinks |

Stopping after any phase leaves a working system: there is no state where half of an area exists in both places.

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Missing permission check on an endpoint | Route × role matrix generated from the router table; fail CI on any route not in the matrix |
| Cross-tenant leak | All queries via the same services/`for_club()`; matrix includes a foreign-club user; no direct table endpoints |
| Validation drift between SPA and server | Server is the only validator; SPA never re-implements business rules |
| Effort blowout | Phase D gate; measured velocity, not the §5 estimate |
| Two UIs for years | "Delete the legacy view in the same PR" rule |
| Accessibility regressions (server-rendered HTML was accessible by default) | Axe checks in Playwright; the Radix-based component set |
| i18n gaps | Server strings arrive translated; CI fails on missing SPA keys |
| Contributor onboarding | One `uv run` + one `npm run dev` command documented; preview environments |

## 10. Decisions needed

1. Do any of the §7 triggers hold today? Which?
2. Is a front-end owner available (in-house or contracted)?
3. React (recommended, shared with Expo) or another framework?
4. Pilot choice: `controlpanel/` first (safe, internal) and which `management` area second?
5. Accept session-cookie auth for the admin SPA (recommended) or require tokens everywhere?
