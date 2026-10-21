# PERFORMANCE.md — Measured performance audit and improvement plan (design)

Status: **findings and proposals, no application code changed.** Branch `claude/architecture-decoupling-research`, 2026-10-21.
Part of the decoupling work: `DECOUPLING.md` (overall), `DECOUPLING-ADMIN.md`, `BACKEND-OPTIONS.md`.

**Decisions this builds on:** Django stays. Existing tables and columns are not restructured
(no rename, drop or type change). Additions (new indexes, tables, columns) may happen later through
ordinary Django migrations. Every item below says which of the two it is; most need no schema work at all.

---

## 1. TL;DR

1. **Most of the avoidable cost is query count, not slow queries.** On a small demo club the lightest
   management page already runs about **20 SQL queries**, the member PWA's `me` and `home` pages run
   **60–67**, and the club-admin sign-up page runs **102**, growing with the number of rows.
2. **Four root causes explain nearly all of it** (§4): navigation "chrome" computed by global
   context processors on every render (including htmx fragments), permission/season checks that are
   recomputed instead of reused within a request, N+1 loops in a handful of list pages, and
   per-request fixed lookups (session, user, member, club).
3. **None of these fixes needs a schema change.** Indexes on the hot tables are already
   thorough; I would not start there (§5.3). Search and photo delivery are the places where an
   *additive* change (trigram indexes, stored thumbnails) is worth considering later.
4. **A decoupled frontend does not fix any of this** and can make the first-launch cost worse: the
   native app's `/context` call already costs 26 queries and `/home` 35, and a SPA would call several
   such endpoints per screen. Fix the shared services first (§7).
5. **Prevent regressions with query-budget tests**, a 15-line pattern per page (§6). Without them these
   fixes decay.

## 2. How this was measured (and what the numbers can and cannot say)

- Fresh scratch database (SQLite) migrated from the 250 migrations and filled with the repository's own
  `seed_demo_data` (a small club: a few families, ~13 pending sign-ups, a handful of events). Django test client,
  logged in as a platform superuser/club admin and as a parent. Caches warmed with one request before counting.
- **Query counts are portable to PostgreSQL. Timings are not**, and the data is tiny. I report counts and
  repeat counts; where a cost scales with rows I say so and base that on reading the code, not on a large-data run.
- Reproduce with `docs/perf/query_audit.py` (documented header, not wired into the app or CI).
- **Not measured:** production traffic, PostgreSQL `EXPLAIN`, p95 latencies, memory, WeasyPrint/XLSX time, the
  scheduler's effect on request latency. The repository already has an opt-in tracer (`rosterchief/perf_logging.py`,
  `DJANGO_PERFORMANCE_LOGGING`); §8 says how to use it to validate this document.

### Headline numbers

| Surface | Pages measured | Result |
|---|---|---|
| Management (club admin), GET pages needing no arguments | 56 returned 200 | median **23** queries, min **21** (even `member_create`, a blank form), max **102** |
| Heaviest management pages | | `signup_list` **102** (71 repeats), `home` **71** cold / **54** warm, `referee_management` **53**, `membership_list` 39, `event_create` 38, `member_list` 35 |
| Member PWA (parent) | 6 | `me` **67**, `home` **60**, `calendar` 54, `payments` 47, `news_list` 45, `forms_list` 43; **23–31 repeats each** |
| Native app API (parent) | 5 | `/context` **26** (9 repeats), `/home` **35**, `/payments` 13, `/news` 10, `/notifications` 8 |
| htmx fragment vs full page (`member_list`) | | **35 vs 35** queries: a partial swap costs as much as a full page |

## 3. What a request costs today (the fixed floor)

Query trace of the lightest authenticated management page after warm-up (20 queries):

| Source | Queries | Where |
|---|---|---|
| Session row | 1 | `django.contrib.sessions` (database session engine) |
| User row | 1 | `authentication/middleware.py:70` (MFA enrolment check) |
| Member row for language | 1 | `members/language.py:34` |
| Club row by slug | 1 | `club/tenancy.py:71` |
| Billing notice | 1 | `management/context_processors.py:329` → `billing/services/notices.py:53` |
| Sidebar counters (referee/official games, sign-up queue, registrations awaiting) | **≈ 10–11** | `management/context_processors.py:416–421` → `management/views.py:3923–3926`, `7727–7728`, `registration/services/invoicing.py:327` |
| Notification bell + others | ≈ 3 | `management/context_processors.py:431+` |

So **more than half of a trivial page's queries are navigation chrome**, computed whether or not the
response includes the navigation (htmx fragments do not, but still pay: 35 vs 35 above).

## 4. Findings

Each finding: evidence, fix, expected effect (an estimate unless stated), risk, schema impact.

### P1. Context processors run every query on every render, fragments included — HIGH value, LOW risk
- **Evidence:** 17 context processors in `settings.TEMPLATES` (the `management.*` and `controlpanel.*` ones query the database); `sidebar_counters`, `billing_notice`, `notification_bell`,
  `is_admin`, `management_position`, `feature_sections`, `news_permissions` all query. `member_list` as an htmx fragment: 35 queries, same as the full page.
- **Fix, in order of effort:**
  1. Return **lazy values** (`SimpleLazyObject`/callables). Django evaluates a context variable only when the
     template reads it, so a fragment that does not render the nav pays nothing.
  2. **Cache the counters** per club (and per role set where the number differs) in Redis for 30–60 s, with
     explicit invalidation optional. The doc strings already note these are "always-visible counts"; a minute of staleness is acceptable for a badge.
  3. Make the notification-bell unread count one cached query per member, invalidated on notification create/read.
- **Expected:** management floor 20 → roughly 6–8 queries; htmx fragments → single digits.
- **Risk:** stale badge for up to the TTL; cache keys must include the club id (tenancy).
- **Schema:** none.

### P2. Permission and season lookups are recomputed instead of reused — HIGH value, LOW–MEDIUM risk
- **Evidence (`mobile:me`, 67 queries):** `club_clubrole` ×8 (`can_manage_members` → `is_club_admin` and `is_member_admin`, each its own query, `club/services/access.py:61/73/93/99`),
  `club_season` ×6–7 (`current_season` → `Season.covering`, `club/models.py:370`, called from `teams_staffed_by`, `is_coach_manager`, views),
  `members_member` ×5–7 (several unmemoized lookups), `club_refereemanager` ×4–6.
  `get_club_admin(request)` already shows the right pattern (`cached_on_request`); the lower-level functions callers use directly bypass it.
- **Fix:** resolve the user's role set for the club **once per request** (one `ClubRole` query) and answer every
  `is_*`/`can_*` predicate from it; memoize `current_season(club)` per request (or on the tenant `Club` instance for that request); route `get_request_member` through the same cache for the language middleware.
  Keep the public function signatures so call sites and the 5,500 tests do not change.
- **Expected:** `mobile:home` 60 → ~30–35, `mobile:me` 67 → ~30; the same functions back `appapi`, so `/context` and `/home` improve too.
- **Risk:** stale permission within one request is harmless; do **not** cache across requests (role changes must apply immediately).
- **Schema:** none.

### P3. N+1 query loops in list pages — HIGH value (grows with data), LOW risk
Evidence from stack traces:
- **`management:signup_list`: 102 queries for 13 rows.** `club/services/onboarding.py:35–37` (`checklist_for`) runs 2 queries **per row, twice** (once from the view `management/views.py:7785`, again from `blocking_event_kinds` `:7786`), plus 13 `members_member` per-row lookups (`:7800`). At 200 pending sign-ups that is on the order of 1,500 queries (extrapolated from the linear pattern).
  `annotate_onboarding_status` (`onboarding.py:110–114`) already shows the set-based approach.
- **`management:member_list`:** `Member.contact_email` (`members/models.py:174`) and `Family.__str__` (`:58`) issue a query per row (4 + 4 on a 4-row page, linear). `select_related("user")` and a prefetched family-membership list remove them.
- **`management:referee_management`:** about 8 sequential `events_event` queries in `get_context_data` (`management/views.py:4015–4027`) that overlap; collapse into one annotated/prefetched query.
- **Fix:** the three above, then add the **scale test** from §6 so the next N+1 is caught by CI.
- **Expected:** `signup_list` 102 → ~12–15 and flat in the number of rows; `member_list` flat.
- **Schema:** none.

### P4. Dashboards and app bootstrap recompute everything per request — MEDIUM–HIGH value
- **Evidence:** `management:home` = 54–71 queries from `controlpanel/services/statistics.py` (`club_statistics`, `club_attention`, `fee_aging`, `renewal_rate`); `mobile:home` = 60; app `/context` = 26 and `/home` = 35.
- **Fix:** short-TTL Redis caching (60 s) of the club-level aggregates keyed by `(club, season)`, separated from the per-user parts; collapse the ~10 `count()` queries on `ClubMembership` into one `aggregate()` with conditional `Count(filter=…)`.
  For the native app, `/context` is requested at launch and foreground: add the `ETag`/`If-None-Match` already planned in `API.md` §15.
- **Expected:** `management:home` 54 → ~15 on a cache hit, `/context` 26 → ~8.
- **Risk:** staleness; define the TTL per number (money figures stay uncached or very short).
- **Schema:** none.

### P5. Fixed per-request lookups — LOW–MEDIUM value, LOW risk
- **Evidence:** every authenticated request pays session + user + member-for-language + club-by-slug = 4 queries before any view code.
- **Fix:** `SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"` (Redis is already the cache; reads served from cache, writes still persisted, **same table**): −1. Cache `Club`-by-slug for 30–60 s with invalidation on `Club` save (the tenancy middleware is the only reader): −1. Reuse the request-cached member for language instead of an extra query: −1.
- **Expected:** −3 queries on every request across all surfaces, including the app API.
- **Risk:** cache invalidation for club settings and maintenance mode (a club under maintenance must close at once: keep that flag out of the cached object or give it a very short TTL).
- **Schema:** none.

### P6. Member photos: every avatar is an authenticated Python request streaming the original — MEDIUM–HIGH value
- **Evidence:** `management/templates/management/_avatar.html` renders an `<img>` per person pointing at `members:player_photo|profile_photo`; the view (`members/views.py:60–95`) does `get_object_or_404`, a photo-permission check (several queries) and `FileResponse`s the **stored original through a gunicorn thread**. Uploads are accepted up to 10 MB (`rosterchief/uploads.py`). I found **no resizing or thumbnail step** for member photos (the only image code reads dimensions of logos, `club/services/images.py`; the bulk importer only validates).
  A 25-row member list therefore issues 25 Python requests for up to 25 originals to paint 32-pixel circles.
- **Fix (additive, no schema change):** (1) generate renditions (e.g. 96 px and 384 px WebP) at upload or lazily on first request, stored next to the original under a deterministic name; `Member.photo_url` selects the rendition (`?w=96`); (2) conditional GET (`ETag`/`Last-Modified`, `304`) so repeat views transfer nothing (the existing `?v=` version token already supports long `max-age`); (3) later, hand the byte transfer to the proxy or object store after authorization (S3 presigned redirect; reverse-proxy internal redirect is **UNVERIFIED** for the current Caddy build).
- **Expected:** list pages move from megabytes to kilobytes and free request threads.
- **Risk:** private-photo rules must stay identical (`can_view_member_photo`); renditions inherit the original's privacy and must be deleted with it (GDPR deletion path).
- **Schema:** none if renditions are files named by convention; a column would be an addition.

### P7. Search uses OR-ed `icontains` across joins — MEDIUM value (Postgres validation required)
- **Evidence:** `management/views.py:477` and `:588–594` combine 4–8 `__icontains` querysets with `|` across `member`, `user`, `family_memberships`, `batch`. `LIKE '%x%'` cannot use B-tree indexes, and the OR over joins multiplies rows and needs `DISTINCT`.
- **Fix:** one query with `Q` objects and `Exists()` subqueries instead of OR-ing querysets; and, as an **addition**, `pg_trgm` GIN indexes on the searched text columns (first/last name, email) so the same queries use an index. Needs the `pg_trgm` extension on PostgreSQL (SQLite dev ignores it).
- **Expected:** unknown until profiled; matters only past a few thousand members per club. **Validate with `EXPLAIN (ANALYZE)` on production-sized data before adding any index.**
- **Schema:** additive (index + extension) — allowed later.

### P8. Process model: one pool serves requests, scheduler jobs, exports and file streaming — MEDIUM value, needs an ops decision
- **Evidence:** `Dockerfile` runs `gunicorn --workers 2 --threads 4` (8 concurrent requests per container); the APScheduler leader thread runs jobs (`call_command`) inside a web worker (`features/scheduler.py`, `gunicorn.conf.py`); WeasyPrint PDFs, openpyxl exports and `FileResponse` photo streaming each hold a request thread; `--max-requests 500` recycles workers (and the scheduler leader) regularly. Compose deliberately removed a separate worker (documented in `compose.yaml`).
- **Fix options:** (a) measure thread saturation first (§8); (b) run the same image a second time as a "jobs" container with no HTTP traffic and the scheduler enabled only there, leaving web workers to requests; (c) raise workers/threads in proportion to RAM; (d) cache generated PDFs (invoices are already cached).
- **Risk:** reverses a documented simplicity decision, so it needs an explicit yes/no from the owner. Only do it if saturation shows up in the data.
- **Schema:** none.

### P9. Things already good (no action recommended)
- **Indexing on the hot models is already deliberate:** `Event(club, start)`, `ClubMembership(club, season, kind|status)`, `Notification(club, member, read_at)`, `Attendance(event, status)`, per-club unique constraints (72) that double as indexes, and Django's automatic FK indexes. I found no obviously missing index on the pages I measured; the problem is query count. Treat new-index ideas (for example `Notification(club, member, -created)` for the bell's 8 newest) as hypotheses to confirm with `EXPLAIN` on Postgres.
- **Static delivery:** hashed, compressed WhiteNoise manifest in production; Caddy `encode zstd gzip`; one Tailwind bundle per surface; `chart.js` (208 KB) loaded on only 3 pages; fonts are subset woff2 with `font-display: swap`.
- **Public API:** per-club 30 s cache and `Cache-Control` already in place (`api/cache.py`, `api/middleware.py`). A cheap extra is conditional responses and `s-maxage` for the Cloudflare edge.
- **DB connections:** `CONN_MAX_AGE=60` with health checks. A connection pooler matters only after adding servers.

### P10. Smaller observations
- Meta `ordering` that spans joins (for example `["team", "member__last_name", "member__first_name"]` on `TeamMembership`, `StaffAssignment`, `ClubRole`) adds a join and sort to **every** queryset on those models that does not call `order_by()`. Use `.order_by()` explicitly in queries that do not need a sort (counts and `exists()` already clear it).
- `seed_demo_data` runs in ~12 s on SQLite and is a good fixture for the scale test in §6.

## 5. What does *not* need a schema change vs what is an addition

| Item | Schema | Notes |
|---|---|---|
| P1 lazy/cached context processors | none | Redis cache |
| P2 per-request roles/season | none | pure code |
| P3 N+1 fixes | none | `select_related`/`prefetch_related`/`annotate` |
| P4 cached aggregates, ETags | none | |
| P5 `cached_db` sessions, cached club lookup | none | same `django_session` table |
| P6 photo renditions | none (files), optional column later | addition if wanted |
| P7 trigram search | **addition** (extension + indexes) | validate with EXPLAIN first |
| P8 process split | none | ops change |

### 5.3 On indexes
Do not add indexes speculatively. The audit found query-count problems, and an index does not help a loop of
600 tiny indexed lookups. When profiling shows a slow *single* query (via `pg_stat_statements`, §8), add the
narrowest index that serves it as an ordinary additive migration (`CREATE INDEX CONCURRENTLY` through
`AddIndexConcurrently` on PostgreSQL, so it does not lock the table in production).

## 6. Stopping regressions: query budgets and scale tests

1. **Query-budget tests** for the ~20 most-used pages and the main app API endpoints:
   ```python
   with self.assertNumQueries(N):   # N = current count after the fixes + small headroom
       self.client.get(url, HTTP_HOST=...)
   ```
   Prefer a ceiling helper (`assert_queries_at_most(N)`) so a legitimate +1 does not break the build but +20 does.
   `appapi` already has a route-table test pattern to copy.
2. **Scale test**: render a list page with 5 rows and with 50 rows and assert the **query counts are equal**. This is the
   single check that finds N+1s regardless of where they appear. Use `seed_demo_data`-style factories.
3. **htmx rule**: a fragment response must not run the navigation counters (assert fragment query count ≤ some small number).
4. Include these in the `--fast` tier (they add coverage of the paths they guard); keep the budget numbers in the tests, not in docs.

## 7. How this fits the decoupling plan

- **Order matters:** P1–P5 first, because every frontend (server-rendered, a React admin, the Expo app) calls the same
  services and pays the same fixed floor. A SPA that issues three API calls per screen would triple the floor.
- **The admin API proposed in `DECOUPLING-ADMIN.md` removes P1 by construction** (no template context processors) but only if
  its endpoints reuse the P2 request-scoped permission resolver; a naïve port would reproduce the repeats.
- **App API:** add the P4 `ETag` caching on `/context` and `/home` *before* the native app ships; it is the call made on every launch.
- **Contract tooling** (`DECOUPLING.md` §5.1) gets a performance twin: Schemathesis/OpenAPI runs can also record query counts per route and fail a PR whose count rises.
- **Real-time** (live scores) via SSE (`DECOUPLING.md` §5.5) is independent of all of this.

## 8. Validate on production before and after (the missing data)

1. Switch on `DJANGO_PERFORMANCE_LOGGING=true` for a day (threshold 300 ms) and collect the slowest routes with query counts and template time.
2. Enable `pg_stat_statements`; list the top 20 statements by total time and mean time. This is how any index gets justified.
3. Add a `Server-Timing` header (queries, SQL ms) in staging so browsers and Playwright can chart it.
4. Record p50/p95 per route group (management, PWA, app API, public API) and gunicorn thread saturation, to decide P8.
5. Re-run `docs/perf/query_audit.py` after each fix and paste the before/after table into the PR.

## 9. Suggested order

| Step | Items | Effort | Why now |
|---|---|---|---|
| 1 | Query-budget harness + scale test (§6) | S | locks in every later gain |
| 2 | P5 sessions + cached club lookup | S | −3 queries everywhere |
| 3 | P1 lazy + cached context processors | S–M | biggest single reduction on management and fragments |
| 4 | P2 per-request roles/season | M | biggest on PWA and app API |
| 5 | P3 N+1s (`signup_list`, `member_list`, `referee_management`) | M | removes the part that grows with data |
| 6 | P4 cached aggregates, `/context` ETag | M | app launch cost |
| 7 | P6 photo renditions + 304s | M | page weight and thread use |
| 8 | Measure (§8), then decide P7 and P8 | – | do not guess these |

## 10. Decisions needed

1. Agree the staleness budget for cached navigation counts and dashboard aggregates (30–60 s acceptable?).
2. OK to switch to `cached_db` sessions and to cache the club-by-slug lookup (with invalidation)?
3. Photos: acceptable to store derived renditions as extra files next to originals?
4. Would a separate "jobs" container (P8) be acceptable if the data shows thread saturation?
5. Can production performance logging and `pg_stat_statements` be enabled for a day to validate this document?
