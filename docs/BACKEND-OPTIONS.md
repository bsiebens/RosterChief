# BACKEND-OPTIONS.md — Changing backend technology under a frozen database schema (design)

Status: **proposal, no code changed.** Branch `claude/architecture-decoupling-research`, 2026-10-21.
Follow-up to `DECOUPLING.md` and `DECOUPLING-ADMIN.md`.

**New constraint (from the owner): the backend technology may change; the database schema may not.**
This document treats that as *no DDL at all*: no new or altered tables, columns, constraints,
indexes, triggers, policies or extensions, by any tool, in any release. It supersedes the softer rule in
`DECOUPLING.md` §6 ("expand → migrate → contract"), which only applies if the owner later relaxes the freeze.

Measured from the tree on this date: 57.5k lines of non-test Python, 64.4k lines of tests (≈5,500
tests), 118 model classes, 169 foreign keys, 250 migrations. Claims about third-party tools below come
from general knowledge and were **not re-verified in this session** unless a source is linked; they are
marked **UNVERIFIED** where a decision depends on them.

---

## 1. TL;DR

1. A frozen schema removes the *usual* biggest cost of a backend rewrite (data migration and
   cut-over downtime) and makes **instant rollback by routing** possible. It does **not** remove
   the other big cost: re-implementing ≈57k lines of domain behaviour whose rules live partly in
   Python, not in the database (§3).
2. Every credible new backend can read and write the existing tables. The decision is therefore not
   "can it use the schema" but **"what do we gain for re-implementing the behaviour"**.
3. **Ranking for this project:** (1) stay on Django, add the schema-freeze guard and a parity
   harness (cheap, valuable under every option); (2) TypeScript backend, *only* if the admin and the
   native app are also TypeScript and one language is a goal; (3) .NET or Go only if the team already has that expertise. Rust, JVM,
   Rails, Phoenix, FastAPI-with-SQLAlchemy and data-layer products (Hasura, PostgREST, Directus, Supabase) are not
   recommended (§5).
4. The way to change backend safely is a **per-club strangler**: tenancy is by subdomain, so Caddy can
   route club-by-club to the new backend against the *same* database, and flip back in seconds (§6).
   Auth stays in Django until last.
5. Do this first regardless of the choice: **schema-freeze CI guard**, **invariant inventory**, and a
   **black-box parity harness** (§7). They cost days, de-risk any option, and are the only way to know a
   rewrite is behaviour-identical.

## 2. What "frozen schema" means in practice

Because Django owns the schema today, freezing it has consequences even if Django stays:

| Thing | Effect of the freeze |
|---|---|
| `manage.py makemigrations` | Any model change that produces a migration is rejected. Models can only change in ways Django treats as no-ops (Python-side defaults, `verbose_name`, choices *labels*, validators). Note: changing `choices` or `default` can still emit a migration; the guard catches it. |
| `django_migrations` table and the 250 migration files | Kept as the record. A fresh database (dev, CI, a new environment) is built by running them, or from a **baseline `schema.sql` snapshot** (§7.1) once Django is gone. |
| Row-based tenancy | Good fit: a new club needs rows, never DDL. |
| RLS (`DECOUPLING.md` §5.7) | Policies are DDL: **dropped** under a strict freeze. |
| Django Tasks DB backend (`DECOUPLING.md` §4.4) | Its backend adds tables: **dropped**; Redis backend or APScheduler only. |
| Delta-sync tombstone table (`API.md` §16-8) | New table: **dropped**; use the "re-fetch window" option the doc already recommended for v1. |
| Indexes for slow queries | Cannot be added. Performance work is query/caching only. This is a real cost (§8). |
| Fixing a bad constraint or column type | Not possible in this program; park it as a separate decision. |

## 3. What a new backend must reproduce (the real cost)

The schema is the *storage contract*; the behaviour contract is spread over Django features. Counts
of where behaviour hides (non-test code):

| Behaviour | Where | Count | Why it matters to a rewrite |
|---|---|---|---|
| DB-enforced rules | `UniqueConstraint` 72, `CheckConstraint` 11 | 83 | **Free**: the database keeps enforcing them for any backend |
| Python-only rules | `clean()` / `validate_club_scope` (cross-club FKs), `unique_slugify` (racy check-then-insert), form `clean()` | many | **Not** in the DB. A new backend must re-implement and test each; a miss silently corrupts tenancy |
| Side-effects on save | custom `save()` 18, signals 46, `on_commit` 3 | 67 | Notifications, role sync (`ARCHITECTURE.md` §3.4), fee status. Invisible in the table definitions |
| Concurrency semantics | `select_for_update` 15 | 15 | Same lock order and scope needed to avoid double-booking, double-charging |
| Defaults Django supplies | `default=`, `auto_now(_add)`, `uuid4`, `NOT NULL` without a DB default | everywhere | Django does **not** create database defaults. Every insert from another backend must supply each such column itself, or it fails or stores wrong values |
| Encodings | `FileField/ImageField` 33 (relative storage paths, `upload_to` functions), `PhoneNumberField` 7 (E.164 strings), `CountryField` (ISO codes), `JSONField` 20 (shapes), `TextChoices` (stored string values), timestamps (UTC, tz-aware), decimals | – | Must be byte-compatible with what Django wrote and reads |
| Generic relations | `GenericForeignKey`/`ContentType` 6 | 6 | Rows carry `django_content_type` ids; the new backend must keep using that table |
| Auth data | Django password hashes (PBKDF2 and others), `django_session`, allauth tables (emails, MFA authenticators incl. TOTP, recovery codes, WebAuthn), JWT sessions (`HEADLESS_TOKEN_STRATEGY` JWT, stateful validation) | – | Hash formats are documented and portable; the allauth MFA/WebAuthn storage formats and the JWT stateful validation are Django-library-specific (**UNVERIFIED** how portable) |
| Feature flags | `waffle` tables | – | Reader needed |
| Admin and ops | `django.contrib.admin` registrations 94, management commands (≈25 scheduled/ops jobs), APScheduler with cache-based leader lease | – | Platform tooling and jobs must be ported or kept running in Django |
| Documents | WeasyPrint HTML→PDF (invoices, previews), openpyxl XLSX, iCalendar | – | Layout fidelity is the risk, not the data |
| Localisation | gettext catalogues (`marketing/locale/nl`), `gettext_lazy` everywhere | – | `.po` files are portable; string extraction and lazy evaluation per request are not |
| Business logic outside views | `services/` ≈15.7k lines (events, club, shop, registration, notifications, billing…) | – | The bulk of what must be re-written and re-proven |
| Behavioural spec | ≈5,500 Django `TestCase` tests, HTML-asserting in management/mobile | – | **Not portable**; only black-box API-level cases are reusable (§7.3) |

Consequence: a rewrite is dominated by *re-expressing and re-proving 50k+ lines of rules*, whichever
language is chosen. A frozen schema makes it **safe to do gradually**; it does not make it small.

## 4. Evaluation criteria

1. Reads and writes the existing tables with no DDL, ideally with typed code generated from the live schema.
2. Makes tenant scoping hard to forget (one place, enforced by default).
3. Cost to port ≈57k lines of rules and prove parity.
4. Fit with the front ends being proposed (React admin, Expo app, generated OpenAPI clients).
5. Operations on the existing footprint (single small server, Docker, Postgres, optional Redis, S3; `DEPLOYMENT.md` sizing).
6. Team skills and hiring.
7. Documents, spreadsheets, push, email, i18n support without heroics.

## 5. Options

| # | Option | Schema access | Tenancy | Port cost | Fit / notes | Verdict |
|---|---|---|---|---|---|---|
| 1 | **Stay: Django + ninja** | native | `ClubScopedModel`, middleware | none | Everything exists; guard keeps the freeze honest | **Default** |
| 2 | **TypeScript**: Fastify/Hono/NestJS + Drizzle, Kysely or Prisma | `drizzle-kit pull`, `prisma db pull`, `kysely-codegen` introspect the live DB (**UNVERIFIED** on this exact schema) | Not automatic; build a scoped repository layer so every query takes a club. Prisma client extensions or Drizzle/Kysely wrappers can inject it | High | One language with a React admin and an Expo app, shared types from OpenAPI/Zod. Documents: PDFs via headless Chromium or React-PDF (layout differs from WeasyPrint), XLSX via exceljs. Auth: custom code to read allauth tables and verify Django hashes. No equivalent of Django forms/formsets/admin | **Only if** TS everywhere is a goal |
| 3 | **.NET**: ASP.NET Core + EF Core database-first | `dotnet ef dbcontext scaffold` is a first-class workflow; **global query filters** make club scoping automatic (my strongest candidate for tenancy ergonomics) | Excellent | High | Very mature; QuestPDF/ClosedXML; strong auth stack with custom Django-hash verifier; new language for most Django teams; no shared language with RN/React | Viable if the team knows it |
| 4 | **Go**: chi/echo + sqlc + pgx | `sqlc` generates typed Go from the *live SQL schema*, ideal for "SQL is the contract" | By convention only | Very high (no forms/ORM; hand-written queries and mappers) | Single static binary, tiny memory, good ops | Only with in-house Go skills |
| 5 | **Python, non-Django**: FastAPI/Litestar + SQLAlchemy 2 (`sqlacodegen`/automap) | good | Manual | High | Closest idiom to current code, but gives up Django forms, admin, allauth, migrations and i18n for little benefit; async is not a bottleneck here (§8) | Not recommended |
| 6 | **Rust**: axum + sqlx (compile-time-checked queries against the live DB) | excellent assurance | by convention | Very high | Highest correctness, slowest delivery, narrowest hiring | No |
| 7 | **JVM**: Kotlin/Spring or Ktor + jOOQ | `jOOQ` codegen from DB | by convention | High | Heavy footprint for the small-server sizing in `DEPLOYMENT.md` | No |
| 8 | **Rails / Laravel / Phoenix** | ActiveRecord/Eloquent/Ecto can map existing tables | plugin-dependent | High | Same productivity class as Django: all cost, no gain | No |
| 9 | **Data-layer products**: Hasura, PostgREST, PostGraphile, Directus, Supabase | introspect an existing DB without DDL; the freeze makes them *more* attractive than before | Must be re-expressed as their permission rules (or RLS, which is DDL and therefore excluded) | Medium for CRUD, but object-scoped roles (coach via `StaffAssignment`, parent via `FamilyMembership`) and side-effects (notifications, fees, invoices, 46 signals) still need a custom service, so you run **two** backends | Duplicates RBAC in a second place | Reject for the core; a read-only reporting layer on a replica is a separate, optional idea |

Notes:
- "Port cost" is relative; none is below weeks-to-months of focused work for 57k lines of rules, and I
  did not estimate in days because the work is dominated by proving parity, which is only measurable
  with the harness in §7.3.
- Choosing a language for the backend that differs from the front end is normal and cheap *because the
  contract is OpenAPI*. Language unification is a team-organisation benefit, not a technical one.

## 6. Migration approach that the frozen schema enables

### 6.1 Strangler by club, then by domain

- Caddy routes by `Host` (subdomain = tenant, `club/tenancy.py`). Add a routing table "club → backend".
  A pilot club's club-plane requests go to the new backend; every other club stays on Django.
- **Rollback is a routing change**, because both backends read and write the same unchanged tables
  and the same rows' invariants hold under either.
- Constraint: both backends must preserve every invariant, including the Python-only ones (§3) and the
  side-effects (notifications, role sync). Parity must be proven *before* a real club moves.
- Within a migrated club, move by domain (read-only endpoints first, then writes, then jobs). For any one aggregate,
  keep a **single writer** at a time; the other backend may read it. Domain ownership is a table in the repo, reviewed per phase.

### 6.2 What stays in Django longest

- **Auth plane** (login, MFA, passkeys, JWT issue, sessions, password reset): users are global across
  clubs and the app token works across base domain and club hosts (`API.md` §4). Keep Django as the identity provider; the new backend validates the tokens (shared
  signing key and the stateful-validation check, **UNVERIFIED** details in allauth's JWT strategy).
- **Scheduler and management commands**: run unchanged against the same DB (the leader lease uses the cache,
  `features/scheduler.py`). Port job by job; a new backend must take the same lease or the jobs must be
  moved atomically.
- **Platform admin** (`controlpanel`, Django admin): last; lowest value to rewrite.
- **Marketing site, public API (`/api/v1/`)**: independent, can move at any time or stay.

### 6.3 Cutover shape

```
Client ──► Caddy ──(Host → backend table)──┬──► Django (legacy, IdP, scheduler)
                                           └──► New backend (migrated clubs / domains)
                      both ──► same PostgreSQL, same schema, same Redis, same S3 bucket
```

Shared S3 paths (`upload_to` results) and cache key formats (django-redis prefixes) must be written
compatibly or isolated per backend where sharing is not needed.

## 7. Do-first work (benefits every option)

### 7.1 Schema-freeze guard (CI)
- Commit a golden `schema.sql` (`pg_dump --schema-only --no-owner`, normalised) and a CI job that
  dumps the schema of a database built by the current code and **fails on any diff**. Use a schema-diff
  tool (migra, Atlas, or plain normalised `diff`; tool choice **UNVERIFIED**).
- Add `makemigrations --check` to fail on any new migration, and a rule that the migrations
  directory is read-only.
- Once Django is no longer the schema builder, `schema.sql` plus a one-line "apply baseline" script
  builds fresh databases, and the guard keeps comparing against it.

### 7.2 Invariant inventory
A checked-in table: for each model, which rules are enforced by (a) DB constraint, (b) Python only,
(c) signal/save side-effect, with the code location and the test that pins it. It is the porting
checklist for a rewrite and also exposes gaps worth testing in Django today (for example the
check-then-insert `unique_slugify` race, which the DB constraint catches only if it exists).

### 7.3 Black-box parity harness
- **Characterization suite**: record request/response pairs from `appapi` (and, if the admin API is
  built, `manage/v1`) against Django using `seed_demo_data` fixtures; replay against any other backend
  and diff status, headers that matter, bodies, and **the resulting database rows**.
- **Row-level diff** after each scenario (excluding timestamps/ids) is the real parity oracle, since the schema
  is shared.
- **Shadow mode**: for read endpoints, the proxy duplicates traffic to the new backend and logs response
  diffs without serving them.
- Spec-driven fuzzing (Schemathesis) against both implementations.
- Only black-box tests port. The HTML-asserting Django tests (≈19k lines in management/controlpanel) are retired with
  the code they test.

### 7.4 Contract and tooling
Everything in `DECOUPLING.md` §5.1–5.2 still applies (generated clients, breaking-change gate, split `views.py`,
`--changed` test mode) and is backend-agnostic.

## 8. Does a new backend buy anything technical?

Evidence needed before a language change is justified; none of it is in the repo yet:
- **Performance**: unknown. `rosterchief/perf_logging.py` exists but I did not see captured p95s. With an
  unindexable schema (§2), slow endpoints must be fixed with query rewrites, caching (Redis is optional and
  available), and `select_related`/`prefetch_related`; these are language-independent. A rewrite does not fix a missing
  index, and the freeze forbids adding one. **Measure before deciding.**
- **Concurrency**: the WSGI + gunicorn + APScheduler design is intentionally simple. Async would help
  only with long-lived connections (live scores); an SSE sidecar covers that without a rewrite (`DECOUPLING.md` §5.5).
- **Hosting cost**: the app runs on a small box; memory-heavy runtimes (JVM) would make it worse, Go/Rust would make it slightly better.
- **Hiring/skills**: the most credible reason to change; it is a team decision, not a performance one.

## 9. Recommended path

1. Adopt the freeze formally: guard (§7.1), invariant inventory (§7.2), parity harness (§7.3). **No language choice needed yet.**
2. Capture performance and error baselines on the current backend.
3. If, after that, a new backend is still wanted, pick it by **team skill first**, then:
   - TypeScript if the admin SPA and Expo app are happening (shared types, one language),
   - .NET if there is .NET expertise (best database-first and tenant-filter ergonomics),
   - Go if there is Go expertise and operations simplicity is the goal.
4. Start with a read-only slice (for example `GET /home`, `/calendar`, `/news`) behind shadow mode for one pilot club,
   then writes, with auth still in Django.
5. Re-estimate after the pilot slice using measured velocity; stop at any phase and keep both backends if needed.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Python-only invariants lost in a rewrite → cross-tenant or corrupt rows | Invariant inventory + row-level diff harness; no club moves until clean |
| Two backends diverge in side-effects (notifications, role sync, fees) | Single writer per aggregate; side-effects ported with the aggregate, not separately |
| Inserts fail or store wrong data because Django-supplied defaults are missing | Per-table insert tests from the new backend; explicit default table generated from the models |
| Auth drift (hashes, MFA, JWT validity) | Keep Django as IdP; port auth last |
| Documents render differently (invoice PDFs) | Visual-diff tests on stored invoices; keep WeasyPrint in Django behind an internal endpoint if needed |
| Missing indexes can't be fixed | Caching and query work; revisit the freeze if a hot path demands an index (that is a separate, explicit decision) |
| Long dual-running period | Phase gate with a written stop/continue decision; domain ownership table |

## 11. Decisions needed

1. Confirm the freeze means **no DDL of any kind** (including indexes, constraints, RLS, new tables). Is an index-only exception acceptable if performance requires it?
2. Which skills does the team (or contractor) actually have: Python, TypeScript, .NET, Go?
3. What is the motivation for changing backend technology: hiring, a single language, performance, cost, or an ops goal? The answer changes the ranking in §5.
4. Is keeping Django as identity provider and scheduler for the long term acceptable?
5. Approve the do-first work (§7) as the next step regardless of the language decision?
