# DECOUPLING.md — Technology options, frontend/backend split, and a faster way to build (design)

Status: **proposal, no code changed.** Branch `claude/architecture-decoupling-research`, 2026-10-21.
Follow-ups: `DECOUPLING-ADMIN.md` (moving management/control panel off server rendering), `BACKEND-OPTIONS.md` (backend technology under a frozen schema).
Companion to `ARCHITECTURE.md` (models, tenancy, RBAC), `API.md` (native-app API) and `DEPLOYMENT.md`.

Where this document and the code disagree, the code wins. Numbers below were measured from the tree
on this date (line counts exclude migrations). Claims about third-party tools come from web searches
on 2026-10-21; many sources were vendor blogs, so items I could not confirm are marked **UNVERIFIED**.

**Hard constraint (from the brief):** the database schema does not change unless a migration/transition
path handles the update. Section 6 turns that into concrete rules.

> **Update 2026-10-21:** the owner has tightened the constraint to a **fully frozen schema (no DDL at all)**, with backend technology now open to change. See `BACKEND-OPTIONS.md`. Under that rule the following items here are withdrawn: PostgreSQL RLS (§5.7), the Django Tasks database backend (§4.4/§5.6), and the expand/contract rules in §6 (kept only as the procedure if the freeze is ever relaxed).

> **Update 2026-10-21 (later): decision recorded.** Django stays as the backend. Existing structure is not restructured (no rename/drop/type change); **additions are allowed later** through normal Django migrations. This relaxes the "no DDL at all" note above: RLS and a database-backed task queue are possible again as additions, but are not planned. Performance work is documented in `PERFORMANCE.md` (measured; most gains need no schema change) and should come **before** any new frontend, since all frontends share the same services.

---

## 1. TL;DR

1. **The backend is already decoupled where it matters.** `appapi/` is a token-authenticated
   django-ninja API (OpenAPI snapshot committed, CI fails when stale) with member **and** coach
   parity with the PWA. `api/` is a public read-only API. Services (`events/services`,
   `club/services`, …) hold the rules. Rewriting the backend would throw away the most expensive,
   best-tested asset: ~5,500 tests, 384 commits in 6 weeks, ~250 migrations of production-shaped schema.
2. **The cost is in the two big server-rendered surfaces**, `management/` (≈12k lines of code
   plus 16k lines of tests, 119 templates, 8k-line `views.py`) and `mobile/` (21k lines incl. ≈8k of tests, 132 templates, a PWA with a service
   worker and an offline queue). They are the part where a decoupled frontend *could* pay off, and
   also where a rewrite is the riskiest and slowest.
3. **Recommendation: evolve, don't replace.**
   - Keep Django, the ORM, the schema, htmx/Alpine/Tailwind for `management/`, `controlpanel/`, `marketing/`.
   - Make the **app API the single contract** (the only supported way for any non-Django client to
     talk to the data) and extend it to the surfaces that need rich clients.
   - Build the **native/mobile client from the OpenAPI spec** (Expo/React Native + TypeScript is the
     best fit; see §4) instead of porting the PWA's 132 templates. The PWA stays until the app
     reaches parity, then is retired or kept as a thin shell.
   - Spend the "speed up working" budget on **developer-loop tooling** (§5), which is cheaper than any
     rewrite and benefits whichever frontend wins.
4. **Do not** introduce a second backend (FastAPI/Node/Go), a BaaS (Supabase/Hasura/Directus) or
   GraphQL. Each would duplicate RBAC/tenancy in a second place, and tenancy + RBAC is the part of
   this system that must have exactly one implementation (§3).

---

## 2. Where we are (measured)

| Layer | Today | Notes |
|---|---|---|
| Backend | Django ≥6.1, Python 3.14, PostgreSQL (SQLite dev), Redis cache, APScheduler in-process, gunicorn WSGI | no ASGI/channels in use; `rosterchief/asgi.py` exists |
| Tenancy | Row-based, `club` FK on `ClubScopedModel`, subdomain → `request.club` (`club/tenancy.py`) | `User` is the only global model |
| RBAC | Service layer, `club/services/access.py` + object scopes (coach, parent) | not Django groups/guardian |
| APIs | `/api/v1/` public read-only (ninja, wildcard CORS); `/api/app/v1/` authenticated (ninja, 17 routers, no CORS); `/api/auth/app/v1/` (allauth headless, JWT, MFA, passkeys) | OpenAPI snapshot at `docs/app-api/openapi-app-v1.json`; push via SNS |
| Admin UI | `management/` ≈12k lines of code (8k `views.py`, 2.2k `forms.py`) + 16k lines / 1,316 tests, 119 templates, htmx on 74 templates, Alpine on 23 | Tailwind 4 + daisyUI 5, 6 separate CSS bundles |
| Member PWA | `mobile/` 21k lines (incl. ≈8k of tests), 132 templates, `sw.js`, ~43 KB of hand-written JS (offline queue, sheets) | the largest hand-maintained client-side logic |
| Platform admin | `controlpanel/` 6k lines, 29 templates | |
| Tests | ~5,500 `def test_`, parallel runner, `--fast` tier (3,124 tests on the skip list), CI shards | full suite ≈ 25–40 s on 10 cores per the runner docstring (will have grown) |
| Build | `npm run build` produces 6 Tailwind bundles; multi-stage Dockerfile; images built on GitHub runners | |

Two observations that shape everything below:

- **The API-first work is further along than the repo's top-level description suggests.** `API.md`
  phases 0–4 report done on the server side (member read/write, commerce, coach). The remaining
  work is the *client* and phase 5 hardening, not a new API.
- **Management has no API equivalent** and `API.md` lists it as a non-goal. That is the one place
  where "decoupling the frontend" would mean writing a lot of new API surface.

---

## 3. What must stay singular

Whatever is built, these live in exactly one place — Django — and every client goes through it:

1. **Tenant resolution and scoping** (`ClubTenantMiddleware`, `ClubScopedModel`, `.for_club()`).
   A second backend (or a BaaS talking to Postgres directly) would need to re-implement row-level
   scoping; a single missed `club_id` filter is a cross-tenant data leak. PostgreSQL RLS could back
   this up (§5.7) but should not replace it.
2. **Authorization** (`club/services/access.py`, `appapi/scope.py`). Multiple implementations of
   "may this parent see this child's photo" will drift. `API.md` §11 already extracted the rules into
   services for exactly this reason.
3. **Money and registration rules** (`shop`, `billing`, `registration`, `club.fees`).
4. **Migrations.** One migration history, owned by Django (§6).

---

## 4. Options investigated

Scoring is for *this* project: small team, Django expertise, ~250 migrations of schema, multi-tenant,
a finished OpenAPI contract, two big server-rendered UIs, Dutch/English i18n.

### 4.1 Frontend for the member surface (replacing or complementing the PWA)

| Option | What it is | Pros | Cons / risks | Verdict |
|---|---|---|---|---|
| **A. Keep PWA (status quo)** | Django templates + htmx + Alpine, service worker | zero rewrite; shares i18n, auth, templates with the rest | 43 KB bespoke offline JS to maintain; no store presence; weakest push on iOS; `mobile/` is ≈13k lines of Python code plus 132 templates to evolve in lock-step with the API | baseline |
| **B. Expo / React Native + TypeScript, generated client** | native apps from one TS codebase; typed client via `openapi-typescript`/`orval`/`hey-api` from `openapi-app-v1.json` | store presence, real push (backend already has SNS gateway), offline storage, large hiring pool, shares TS tooling with any future web client; generated types give contract tests for free | second language/toolchain; store review (payments posture is already pay-at-pickup, `API.md` §12); two native build pipelines (EAS reduces this) | **recommended for the native app** |
| C. Flutter | Dart UI toolkit | very consistent UI across platforms | Dart is a new language for the team; OpenAPI Dart generators exist but I did not verify their quality (**UNVERIFIED**); smaller overlap with web skills | viable second choice if pixel-identical UI matters |
| D. Capacitor wrapping the existing PWA | WebView shell + plugins | fastest route to a store listing; reuses 132 templates | still a web UI in a shell; cookies/session story vs the token-only API (`API.md` says no cookies); native feel is the weakest. One source ranked it last for mobile-specific work | only as a **stopgap** for store presence |
| E. Native Swift/Kotlin | two native codebases | best platform fidelity | two codebases; `docs/app-api/ios.md` and `android.md` already assume this and are the cheapest to start with if volunteers exist | keep documented; choose only if native specialists exist |
| F. SPA web client (React/Next, SvelteKit, Nuxt) for members | browser app on the same API | typed, componentised | duplicates what the PWA and the app would each do; adds SSR/hosting; cookies vs token again | **not recommended** unless the PWA is retired *without* a native app |

### 4.2 Frontend for management / control panel

| Option | Verdict |
|---|---|
| Keep Django templates + htmx + Alpine + Tailwind | **Recommended.** The admin is form-and-table heavy, role-gated and permission-rich; server rendering keeps every permission decision in Django and avoids writing a ~290-route API plus a SPA (but see `DECOUPLING-ADMIN.md` for the case where that is wanted). |
| React/Next admin on a new management API (`/api/manage/v1`) | Highest cost, lowest return. Only revisit if a specific screen needs rich client state (drag-drop lineup editor, timeline editor). Build that as an **island** instead. |
| Admin frameworks (Refine, react-admin, Django Unfold/Jazzmin) | Unfold-style admin skins don't match the bespoke per-club RBAC UI; Refine/react-admin need the management API anyway. Not recommended. |
| Islands: small Vite-built TS components mounted in templates for 2–3 rich screens | **Recommended where needed** (§5.3). |

### 4.3 Server-driven UI libraries (reduce JS and template boilerplate)

| Option | Notes | Verdict |
|---|---|---|
| **Django 6 built-in template partials** (`{% partialdef %}`, `template#partial` syntax) | Official in Django 6.0. Replaces ad-hoc `_fragment.html` files that exist for htmx swaps (`management/templates/management` alone has 23 underscore-prefixed partial templates). Same HTML, one file per page. | **Adopt gradually**, new code first |
| **htmx 4** | Released 2026-08-28 per its own site (**dates differ across sources**). fetch-based, streaming, morph swaps; **inheritance becomes opt-in**, error responses (4xx/5xx) now swap by default, events renamed `htmx:phase:action`, default timeout 60 s. 2.x stays `latest` on npm and 4.x `next` until early 2027. An `htmx-2-compat` extension and an `upgrade-check` CLI exist. | **Wait until 2027**, then upgrade in one PR using the compat extension. The repo already vendors htmx (`vendor:htmx`), so the upgrade is a file swap plus the migration checklist. 74 templates use `hx-`. |
| Hotwire Turbo / Unpoly | Equivalent capability, but would replace htmx across 74 templates for no new capability. | No |
| django-unicorn / LiveView-style (Reactor, django-htmx-live) | Server state per component, needs sticky sessions/websocket, conflicts with WSGI and the stateless multi-server deployment documented in `compose.yaml`. | No |
| `django-components`, `django-cotton`, `slippers` | Component-style templates. Overlap with template partials + `{% include %}`; extra dependency to maintain. | Evaluate only if partials prove insufficient |
| Alpine.js | Already used (23 templates). Fine. | Keep |

### 4.4 Backend options

| Option | Verdict |
|---|---|
| **Keep Django + django-ninja** | **Recommended.** Ninja is Pydantic/OpenAPI-first, already carries the auth, error envelope, pagination and idempotency helpers (`appapi/`). |
| Migrate ninja → DRF | Pure churn; ninja is the lighter fit for a typed contract. |
| Add FastAPI beside Django | Search results only show directional (blog-level) benchmark claims; for DB-bound CRUD the database dominates. A second service would need its own tenancy, RBAC, auth and ORM or SQLAlchemy mapping of Django's schema. **Rejected.** |
| Move to async (ASGI/uvicorn) | Matters for websockets/streaming (live game timeline, §5.5). Today the only server-initiated channel is push and the 1-minute APScheduler poll. Consider ASGI *only* for a dedicated streaming endpoint (SSE). |
| Django 6 **Tasks framework** | Built-in `@task` API with pluggable backends; Django does not ship workers. Replaces the in-process APScheduler thread *only if* a worker process is acceptable (the deployment deliberately removed Celery/worker/beat, see `compose.yaml`). Reconsider when a job must survive a web-worker restart. Not urgent. |
| GraphQL (Strawberry/Graphene) | Solves client-driven field selection; we already have view-model-shaped REST (`presenters.py`, `home_summary`). Adds N+1 and per-field authorization risk in a tenancy-critical system. **Rejected.** |
| BaaS: Supabase, Hasura, PostgREST, Directus | They expose tables directly; authorization would have to be re-expressed as RLS policies mirroring `club.services.access`. High risk, high duplication. **Rejected** for the core; fine for unrelated tooling. |
| Wagtail for marketing/news | `marketing/` is 391 lines, so no. CLAUDE.md's Ruff comments already "anticipate a Wagtail-style codebase", but nothing needs it. |

### 4.5 Data/infra options that affect speed

| Option | Verdict |
|---|---|
| PostgreSQL RLS as defence in depth for tenancy | **Evaluate (§5.7).** Additive, no table shape change. |
| Redis (already optional) for throttling/ETag cache | Already planned in `API.md` §15 |
| Object storage (django-storages S3) | Already in |
| Edge/CDN in front of static + public API | Cloudflare already in front (`Caddyfile`, trusted proxy) |

---

## 5. Making work faster (independent of the frontend choice)

Ordered by payoff over effort. None of these touch the schema.

### 5.1 Contract-first loop for the app API (highest leverage)
- `docs/app-api/openapi-app-v1.json` is already CI-checked. Add:
  - **Generated TS client** in a new `clients/` (or separate repo) via `openapi-typescript` +
    `openapi-fetch` (or `orval`/`hey-api`). CI job: regenerate and fail on diff, like the existing snapshot test.
  - **Schema-diff breaking-change gate** (e.g. `oasdiff`) on every PR: `API.md` promises additive-only
    within `v1`, and today a human enforces that.
  - **Contract tests from the spec** (Schemathesis against the test server) to find 500s/undocumented
    responses without hand-writing cases. Run nightly, not per PR.
- Result: a client developer never reads Python to learn the API; backend changes show up as TS compile errors.

### 5.2 Faster inner loop on the existing stack
- **Tailwind**: six separate builds (`app`, `controlpanel`, `management`, `mobile`, `mobile-desktop`,
  `marketing`) plus a Dockerfile that must hand-list each `@source` directory (its own comment
  records bugs caused by a missed directory). Collapse to **one entry CSS with per-surface
  `@layer`/`@source` `@import`s, or at least generate the Dockerfile `COPY` list from the `@source` lines**; add a CI check that fails when they diverge.
- **Test loop**: keep `--fast`; add a `--changed` mode (map changed files → app test labels, plus
  dependents via the import graph) so a one-file edit runs seconds, not minutes. `uv run python
  manage.py test --parallel` is already there.
- **Type checking**: adopt `ty` or `pyright` on `services/` and `appapi/` only (the typed boundary);
  Ruff is already in. Gradual, no blanket enforcement.
- **Pre-commit**: ruff + `makemigrations --check --dry-run` + openapi snapshot check, so CI failures
  are found locally.
- **Dev data**: `club/management/commands/seed_demo_data.py` already exists; check it covers multi-season, families, events and shop so client developers get realistic tenant data in one command, and extend it rather than adding a new one.
- **Preview environments** per PR (Compose on the dev box, wildcard subdomain `pr-N.dev.…`). Tenancy by
  subdomain already works on wildcard DNS.

### 5.3 Targeted "islands" for the few rich screens
Candidates seen in the tree: lineup editing, game timeline (`GAME_TIMELINE.md`), bulk row editors
(`bulk-add-rows.js`, `registration-entry-rows.js`, `mobile-reregister-rows.js`), searchable select.
Rather than more hand-written files in `static/js`, introduce a tiny **Vite + TypeScript** build that
emits ES modules mounted by `data-island="..."`, reusing the same JSON endpoints as the app API.
Benefits: types, tests (Vitest), HMR; cost: one more build step (only worth it when ≥3 islands exist).

### 5.4 Template ergonomics
- Adopt Django 6 partials for new HTMX fragments; migrate opportunistically, not in a sweep.
- Break `management/views.py` (8,067 lines) and `mobile/views.py` (2,392) into per-domain modules
  (`management/views/events.py`, …) — pure moves, no behaviour change, shrinking review diffs and merge conflicts.

### 5.5 Real-time without a rewrite
Live score pages currently use an APScheduler minute poll plus htmx `every` triggers (4 occurrences).
For a better live experience use **Server-Sent Events** from a single ASGI endpoint (uvicorn worker
only for that route, or a tiny sidecar reading Redis pub/sub). Do not move the rest of the app to ASGI.

### 5.6 Background work
Stay on APScheduler until jobs must outlive a restart; then adopt Django's Tasks framework with a
database or Redis backend and one extra `worker` container (reversing the Celery removal only if
justified by an incident or a feature, not by taste).

### 5.7 Defence in depth for tenancy (optional, additive)
Prototype PostgreSQL **row-level security** keyed on a `SET LOCAL app.club_id` issued by the
middleware, in *report-only* form first (a test that compares RLS-visible rows to ORM-visible rows).
RLS policies are DDL, not column changes, and are applied by a migration (`RunSQL` with reverse SQL).
Needs a non-owner DB role for the app (owners bypass RLS). SQLite dev can't enforce it, so tests
must still target Postgres in CI (the nightly job already does).

---

## 6. Schema safety rules (the hard constraint)

Any client or backend change must obey these. They are also the checklist for reviewers.

1. **Django owns the schema.** No client, ORM, BaaS or second service runs DDL. One migration history.
2. **Expand → migrate → contract.** A column/table change ships in three releases:
   1. *Expand*: add the nullable column/new table (and write both old+new in code). Backwards compatible with the previous image, so a rolling deploy and an instant rollback both work.
   2. *Migrate*: backfill with a `RunPython` data migration (batched, idempotent, with reverse), or a management command for large tables.
   3. *Contract*: drop the old column in a **later** release, after no running version reads it.
3. **Never** rename in place (`RenameField` breaks the old image mid-deploy). Add new, copy, switch reads, drop old.
4. **Every migration is reversible** (reverse function or `RunSQL` reverse), tested forward and backward on a copy of production-shaped data. `restore-check.sh` and the backup scripts in `deploy/` already exist; add a CI step that migrates a restored anonymised dump.
5. **API stays additive within `v1`** (`API.md` §2). Schema changes must not alter a `v1` response shape; map old shape from new storage in the presenter if needed. Breaking → `v2` beside `v1`.
6. **Unique-per-club constraints** (slugs, invoice numbers) are preserved when columns move.
7. **CI guards:** `makemigrations --check`, a migration linter (e.g. `django-migration-linter` or `squawk` for Postgres lock-heavy operations — **UNVERIFIED which fits Python 3.14/Django 6.1**), and a test that applies migrations N→N+1 on the previous release's data.
8. **No schema change is required by anything in this proposal.** If a later phase needs one (for example a delta-sync tombstone table, `API.md` §16-8, or device-session tables), it follows rule 2.

---

## 7. Recommended architecture

```
                    ┌──────────────────────────────────────────────┐
                    │ Django (single source of truth)              │
                    │  tenancy · RBAC · services · ORM · migrations│
                    └──┬───────────────┬───────────────┬───────────┘
                       │ HTML (htmx)   │ /api/app/v1   │ /api/v1 (public)
   ┌───────────────────┴─┐   ┌─────────┴─────────┐   ┌─┴───────────────┐
   │ management/         │   │ Expo/RN app       │   │ club website /  │
   │ controlpanel/       │   │ (iOS, Android)    │   │ embeds          │
   │ marketing/          │   │ generated TS      │   │                 │
   │ (templates+islands) │   │ client from       │   │                 │
   │                     │   │ OpenAPI           │   │                 │
   └─────────────────────┘   └───────────────────┘   └─────────────────┘
          mobile/ PWA stays during transition, then is retired or frozen
```

- Web admin: server-rendered, enhanced with htmx/Alpine/partials and a few Vite islands.
- Members/coaches: native app on the existing token API; PWA kept until app parity and adoption.
- Public data: `/api/v1/` unchanged.
- One deployable (Django) stays one deployable; the app is built and shipped separately (EAS or CI).

---

## 8. Phased plan

| Phase | Scope | Exit criteria | Schema |
|---|---|---|---|
| **0 — Decide** (this doc) | Choose app tech (RN vs native vs Flutter); confirm management stays server-rendered | Decisions in §10 answered | none |
| **1 — Contract tooling** | Generated TS client in CI, `oasdiff` gate, Schemathesis nightly, pre-commit hook, `--changed` test mode | A PR that breaks `v1` fails CI; client types regenerate | none |
| **2 — Dev-loop wins** | Split `management/views.py`; unify Tailwind builds / Dockerfile source check; extend `seed_demo_data`; PR previews | Measured: CI time, local test-loop time, time to first review | none |
| **3 — App walking skeleton** | Expo app: sign-in with MFA, `/clubs`, `/context`, home, calendar, RSVP, push registration against dev | An internal build reaches Phase 2 parity from `API.md` §14 | none |
| **4 — Parity & pilot** | Member write/commerce, coach flows; one pilot club on the app | Parent + coach checklists pass; crash-free target set | none; any need → §6 |
| **5 — PWA decision** | Retire, freeze or keep `mobile/` based on adoption | Written decision | none |
| **6 — Optional** | htmx 4 upgrade (2027); template partials migration; SSE for live scores; RLS prototype; Tasks framework | each independently justified | RLS = DDL via migration |

Each phase ships independently and can be stopped without leaving the system half-migrated.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| App duplicates logic that lives in PWA views | `API.md` §11 extraction was done; continue treating a missing service as a backend task first |
| Two clients drift (PWA vs app) | Both consume the same services; app consumes only the API; parity table in `docs/app-api/status.md` |
| App-store friction (payments, review) | Keep pay-at-pickup; confirm per `API.md` §16-13 before first submission |
| Team capacity for a second toolchain | Prefer Expo (managed builds), TS only, generated client; pilot before committing |
| Cross-tenant leak via a new surface | Only Django touches the DB; tests assert every new route 404s for a foreign club (already done for id routes in `appapi`) |
| htmx 4 churn | Wait for stable `latest` tag; use compat extension and upgrade-check CLI |
| Estimates for management rewrite are wrong | We aren't proposing one |

---

## 10. Decisions needed from the owner

1. **Native app technology**: Expo/React Native (recommended), native Swift/Kotlin (matches existing `ios.md`/`android.md`), or Flutter?
2. **Who builds the app** (in-house, volunteers, contractor)? This decides between Expo and native more than any technical factor.
3. **PWA fate** once the app ships: retire, freeze, or keep as the web version for members?
4. **Management stays server-rendered?** (Recommended yes; confirm.)
5. **Appetite for RLS** as defence in depth (extra DB role, Postgres required in all CI tiers)?
6. **Is a worker container acceptable** if Django Tasks replaces APScheduler (currently deliberately avoided)?

---

## 11. Sources

- [Django 6.0 release notes](https://docs.djangoproject.com/en/dev/releases/6.0/) — template partials, Tasks framework, CSP
- [Changes in htmx 4.0](https://four.htmx.org/whats-new-in-htmx-4), [htmx 4 migration guide](https://four.htmx.org/docs/get-started/migration), [InfoQ on htmx 4](https://www.infoq.com/news/2026/09/htmx-4-released/) — release dates differ between sources
- [React Native in 2026 vs Flutter](https://pasqualepillitteri.it/en/news/8279/react-native-2026-stack-choices-vs-flutter), [Flutter vs React Native 2026](https://fullscale.io/blog/flutter-vs-react-native/) — vendor/content blogs, market-share numbers conflict; used only for qualitative points
- [DRF vs Django Ninja (HackerOne)](https://www.hackerone.com/blog/django-rest-framework-vs-django-ninja-high-level-comparison), [Django vs FastAPI (DZone)](https://dzone.com/articles/django-architecture-versus-fastapi) — directional only
- Repository: `API.md`, `ARCHITECTURE.md`, `docs/app-api/status.md`, `rosterchief/test_runner.py`, `Dockerfile`, `compose.yaml`, `package.json`, `pyproject.toml`
