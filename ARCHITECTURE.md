# RosterChief — Model & Domain Architecture

Baseline reference for implementing the domain models. This describes the **intended
shape** of the data model: what exists today, what is planned, and the conventions every
app should follow. It is a living document — update it when the model changes.

> **Tenancy: multi-tenant (row-based / shared-schema).** RosterChief is designed as a
> **multi-tenant platform** — one deployment serves many clubs, with **`Club` as the tenant
> root**. Isolation is **row-based**: a shared database and schema where every club-owned
> row carries a `club` FK (via `ClubScopedModel`), and *all* access is scoped to the
> current tenant. The mechanics — tenant resolution, scoping manager, per-club uniqueness —
> are specified in **§2.4**.
>
> ⚠️ **This supersedes `CLAUDE.md`**, which currently states the app is "deliberately *not*
> multi-tenant … there is no `club_id` tenancy." That guidance and the project memory are
> now **out of date** and must be updated to match this document — see the **banner at the
> foot of this file**. Where the two disagree, this architecture is the intended direction.

---

## 1. App decomposition

The `isort` `known-first-party` roadmap lists nine apps. The build split the original
`accounts` app into `authentication` + `club`. Per the decisions in §7, the people models
(`Member`, `Family`) **move out of `authentication` into a dedicated `members` app**, and
two apps (`formbuilder`, `shop`) are added **beyond the original roadmap** — add all new
labels to `known-first-party` in `pyproject.toml` when they land. The target decomposition:

| App              | Status       | Responsibility                                            | Models |
|------------------|--------------|-----------------------------------------------------------|--------|
| `authentication` | **built**    | Login identity + tenancy/role services (global, cross-club) | `User` |
| `members`        | **planned**  | People: person records, families (extract from `authentication`) | `Member`, `Family`, `FamilyMembership` |
| `club`           | **built**    | Tenant root, **season**, season-scoped affiliation, club roles | `Club`, `Season` *(planned)*, `ClubMembership`, `ClubRole` *(planned)* |
| `teams`          | planned      | Teams and season rosters                                  | `Team`, `TeamMembership`, `StaffAssignment` |
| `events`         | planned      | Training / matches / social events + attendance           | `Event`, `Attendance` |
| `news`           | **built**    | Club news: coach_manager-authored, editor-released         | `News`, `NewsPhoto` |
| `pages`          | planned      | Flat CMS pages for the public site                        | `Page` |
| `home`           | planned      | Homepage composition / featured content                   | `HomeConfig` (per-club) or config-only |
| `formbuilder`    | planned      | Admin-defined dynamic forms + submissions + reporting     | `Form`, `Field`, `Submission`, `Answer` |
| `shop`           | planned      | Cart-like shop, orders, payments, PDF invoices            | `Product`, `Cart`, `CartItem`, `Order`, `OrderLine`, `Payment`, `Invoice` |
| `search`         | planned      | Site search (likely no models; index/config only)         | — |

**`User` stays global** (one login identity across the whole platform); everything else
that belongs to a club is tenant-scoped (§2.4). This is why `Member` — a *person within a
club* — lives in its own app and carries a `club` FK, while `User` does not.

**Open decision (§7):** whether to migrate `Member`/`Family` out of `authentication` into
a dedicated `members` app. Recommendation: keep them in `authentication` for now; revisit
only if the app grows unwieldy. The roadmap `members` name is reserved either way.

---

## 2. Shared conventions

These are already established in code — every new model follows them.

- **UUID primary keys.** Inherit `rosterchief.base.UUIDModel` (`id = UUIDField(default=uuid4)`).
  Never expose sequential integer PKs.
- **`ClubScopedModel`** (`rosterchief.base`) adds the tenant `club` FK
  (`related_name="%(class)ss"`) and, under multi-tenancy, a tenant-aware manager + auto
  club-stamping `save()` (§2.4). **Every aggregate-root model inherits it**; leaf rows
  reachable only via a scoped parent (e.g. `Attendance` via `Event`) may inherit scope from
  the parent, though denormalising `club` onto them is recommended (§2.4).
- **i18n everywhere.** Every field gets a `gettext_lazy` verbose name; every model sets
  `verbose_name` / `verbose_name_plural` and a sensible `Meta.ordering`. `TextChoices`
  labels are translated too.
- **`__str__` on every model**, human-readable.
- **Enumerations** use nested `models.TextChoices` (see `FamilyMembership.FamilyRole`).
- **Phone numbers** use `PhoneNumberField` (from `django-phonenumber-field`), nullable.
- **Through models** for many-to-many relationships that carry data (role, jersey number,
  attendance status) — never a bare `ManyToManyField` when the link has attributes.
- **Business logic in `services/`**, not fat models or views (see
  `authentication/services/member_csv_importer.py`). Management commands are thin wrappers
  over services (see `import_members_csv`).
- **Migrations are generated, not hand-edited** (ruff-excluded).

### Naming & relations
- `related_name` is explicit and plural on the "many" side, chosen to read naturally from
  the parent (`club.members`, `family.memberships`, `member.family_memberships`).
- Deletion policy is deliberate per FK: `CASCADE` for owned children,
  `SET_NULL`(+`null=True`) where the child should survive its parent (e.g.
  `Member.user`), `PROTECT` for references that must not silently disappear (planned:
  `Season` on rosters/events — see §5).

### 2.4 Multi-tenancy (row-based, shared schema)

`Club` is the **tenant root**. One deployment, one database, one schema; tenants are
separated by a `club` FK on every owned row and by disciplined scoping of every query.
This is the lightest multi-tenancy model and matches the existing `ClubScopedModel`
scaffolding — no Postgres schemas, no per-tenant databases, no `django-tenants`.

**What gets a `club` FK.** Every *aggregate root* inherits `ClubScopedModel` and so carries
`club` (`Member`, `Family`, `Season`, `Team`, `Event`, `ClubRole`, `Article`, `Category`,
`Page`, `Form`, `Product`, `Cart`, `Order`, `Invoice`, …). Leaf rows reachable only through
a scoped parent (`Answer`→`Submission`, `OrderLine`/`Payment`→`Order`, `CartItem`→`Cart`,
`Attendance`→`Event`, `TeamMembership`/`StaffAssignment`→`Team`) may inherit scope via the
parent — but **denormalising `club` onto them too is recommended** for leak-proof filtering
and DB-level constraints. `User` is the **only** global identity model; it has no `club`.

**People vs. logins under tenancy.** A `User` is one platform-wide login that may belong to
several clubs; a `Member` is that person *within one club*. So:
- `Member.user` becomes a **`ForeignKey`** (not `OneToOneField`) — one user → many members
  (at most one per club): `unique_together (club, user)`.
- `User.get_full_name()` can no longer assume a single member; resolve the member **for the
  current club** (via the tenant context below), falling back to email.

**Tenant resolution → `request.club`.** A `ClubTenantMiddleware` resolves the active club
per request (recommended: **subdomain**, `ajax-united.rosterchief.app`; path-prefix
`/c/<slug>/` is the alternative) and stores it on `request.club` *and* in a context
variable so non-request code (services, management commands) can read it:

```
# rosterchief/tenancy.py
from contextvars import ContextVar
_current_club: ContextVar = ContextVar("current_club", default=None)

def set_current_club(club):  _current_club.set(club)
def get_current_club():      return _current_club.get()
def require_current_club():                        # raises if unset — use in write paths
    club = _current_club.get()
    if club is None: raise RuntimeError("No active club in context")
    return club

class ClubTenantMiddleware:                        # resolves subdomain -> Club, sets both
    ...  # request.club = club; set_current_club(club)
```

**Considered — `django.contrib.sites` for resolution (rejected as the mechanism).**
Django's Sites framework is the obvious "does the batteries-included answer fit?" candidate,
so it was evaluated explicitly:

*What it offers.* A `Site(domain, name)` model, `get_current_site(request)` (Host-header →
`Site`, with a per-process `SITE_CACHE`), `CurrentSiteMiddleware` (sets `request.site`), and
`CurrentSiteManager` (auto-filters models that hold an FK to `Site`). Ecosystem code
(`flatpages`, `redirects`, `sitemaps`, `allauth`) is Site-aware for free.

*Why it does **not** fit as our tenancy mechanism:*
- **Wrong scoping key.** `CurrentSiteManager` filters on a `site` FK; our tenant key is the
  `club` FK on `ClubScopedModel`. Adopting Sites' manager would mean putting a *second* FK on
  every model, or ignoring the manager — either way it buys us nothing over `.for_club()`.
- **A parallel identity table.** `Site` duplicates identity that already lives on `Club`
  (`slug`, domain, name). Two tables to keep in sync, two sources of truth for "which tenant".
- **`SITE_ID` is a single global.** The framework's happy path is *one process = one site*
  (`SITE_ID`). Multi-tenant host resolution requires leaving `SITE_ID` unset and relying on
  `get_current_site`'s exact-domain match — workable, but the setting is a standing foot-gun
  (any library that reads `SITE_ID` silently binds to the wrong tenant), and shells, tasks,
  and tests have no Host header, so they still need our contextvar (`require_current_club()`).
- **Host-only.** Sites cannot express the path-prefix option (`/c/<slug>/`); resolution is
  purely `domain`-based, foreclosing that alternative.

*Verdict.* Keep **`Club` (with `slug` + optional `domain`) as the single tenant root** and
resolve it in `ClubTenantMiddleware` — the resolution logic (Host → `Club`) is a few lines and
avoids the sync/`SITE_ID` hazards. **Optional bridge:** if a Site-aware third party is later
adopted (e.g. `allauth`, `sitemaps`), add a thin `Club.site = OneToOneField(Site)` kept in sync
from `Club.save()`, so the ecosystem gets its `Site` while `Club` stays authoritative — do
this only when such a dependency actually lands, not preemptively.

**Scoping manager.** `ClubScopedModel` gets a tenant-aware manager so day-to-day queries
can't accidentally cross tenants:

```
class TenantQuerySet(models.QuerySet):
    def for_club(self, club): return self.filter(club=club)
    def current(self):        return self.filter(club=require_current_club())

class ClubScopedModel(UUIDModel):
    club = models.ForeignKey("club.Club", on_delete=models.CASCADE, related_name="%(class)ss")
    objects = TenantQuerySet.as_manager()
    def save(self, *args, **kwargs):               # auto-stamp club from context if unset
        if self.club_id is None: self.club = require_current_club()
        super().save(*args, **kwargs)
    class Meta: abstract = True
```

Prefer **explicit** `.for_club(club)` / `.current()` in views and services over a fully
automatic global filter — auto-filtering via context state is convenient but hides tenant
boundaries and bites hard in shells, tasks, and tests. Keep scoping visible.

**Per-club uniqueness.** Every constraint that was globally unique becomes **unique per
club**. Concretely: `Season.name`, all public `slug`s (`Article`, `Page`, `Form`,
`Product`), `Team (season, name)`, jersey numbers `(team, jersey_number)`, and the
human-readable counters `Order.number` / `Invoice.number` are scoped by / allocated per
club. A bare `unique=True` on a tenant model is almost always a bug — use
`UniqueConstraint(fields=["club", …])`.

**Cross-cutting consequences (checklist for every feature):**
- Admin: register a `club` list-filter and scope `get_queryset` for non-superusers.
- Roles are **per-club** — see §3 (global Django Groups don't fit; use `ClubRole`).
- Sequential numbers (invoices) allocate per club inside a transaction — never `count()+1`.
- Tests must set a current club (a `with_club(club)` context-manager helper).
- Config: wildcard host + CSRF for subdomains; see §8.

---

## 3. Roles & access control (RBAC)

Authorization is **service-layer, and per-club** (§2.4). Because roles differ per tenant —
a user can be a treasurer at club A and merely a member at club B — global Django `Group`s
(which are platform-wide) **do not fit**. Roles are stored as tenant-scoped `ClubRole` rows
and *all* permission decisions go through a single service module; no `django-guardian`,
no per-club Group hacks. Django's own permission framework is retained **only** for the
platform-operator layer (`is_staff` / `is_superuser` in Django admin).

### 3.1 Two layers

1. **Platform operators** — `User.is_superuser` / `is_staff`. Global, cross-club; run the
   Django admin, manage the tenant list. Not a club role.
2. **Club roles** — a member's standing *within one club*, stored per tenant. Two kinds:
   - **Club-wide roles** → `ClubRole` rows (below).
   - **Object-scoped roles** → derived from the domain graph, no extra rows:
     - Coach / manager **of a specific team** → `StaffAssignment(team, member, role)`.
     - Parent / guardian **of a specific member** → `FamilyMembership` + the family graph.
     - Purchaser vs. beneficiary → `Order`/`OrderLine` + `ClubMembership`.

### 3.2 `ClubRole` — club-wide role assignments  *(app: `club`)*

```
ClubRole(ClubScopedModel)              # ClubScopedModel -> carries `club` (§2.4)
  member  FK Member (CASCADE, related_name="roles")
  role    CharField (TextChoices: MEMBER | EDITOR | TREASURER | BOARD)
  Meta: unique_together (club, member, role)
```

| Role        | Grants (representative)                                                       |
|-------------|------------------------------------------------------------------------------|
| *Public*    | Anonymous — no row; read-only public site of that club.                      |
| `MEMBER`    | View own + family data, own rosters/attendance, own orders/invoices, submit member-only forms. |
| `EDITOR`    | Manage that club's `news`, `pages`, `formbuilder` content.                    |
| `TREASURER` | Manage that club's `shop`: products, orders, payments, issue/void invoices.   |
| `BOARD`     | Full management of that club: members, roles, all of the above.              |

`news` is the one place a `ClubRole` and a derived role (`COACH_MANAGER`, see below)
share a single workflow rather than each owning a separate permission: drafting is
open to EDITOR/ADMIN *or* any coach_manager, but only EDITOR/ADMIN may publish —
see §5.4.

`COACH` / `TEAM_MANAGER` are deliberately **not** `ClubRole`s — being a coach is always
*of a team*, so it lives on `StaffAssignment` (§5.3). "Is this user a coach at this club?"
= "do they have any `StaffAssignment` on a team in this club?".

### 3.3 The permission service

One module — `club/services/access.py` (or `authentication/services/access.py`) — answers
every authorization question, always taking the club/object as an argument:

```
has_club_role(user, club, role)      -> bool      # ClubRole lookup
roles_in_club(user, club)            -> set[str]  # incl. derived COACH/MANAGER
teams_managed_by(user, club)         -> QuerySet[Team]
members_visible_to(user, club)       -> QuerySet[Member]   # self + family + managed teams
can_edit_event(user, event)          -> bool
can_manage_shop(user, club)          -> bool      # TREASURER or BOARD
```

Views, admin, and templates call these — never re-derive access inline. Each helper scopes
to the given club (§2.4), so a user's powers in club A never leak into club B.

### 3.4 Keeping roles in sync with domain state

`ClubRole` membership is **reconciled from domain facts**, not hand-assigned:

- An **active** `ClubMembership` for the club's current season → grant the `MEMBER` role;
  a lapsed one → revoke. (Season-scoped membership: §5.1.)
- A `StaffAssignment` needs no `ClubRole` — coach status is derived (§3.2).

Implement as `club/services/access.py::reconcile_roles(user, club)`, invoked on the state
changes that matter (membership activation/lapse), so authorization never drifts from data.

---

## 4. Built models (as-is, + planned tenancy changes)

### `authentication`

**`User`** — custom auth model, email is the login (`USERNAME_FIELD = "email"`, no
username). UUID PK. `objects = UserManager()` (email-based `create_user` /
`create_superuser`). **Stays global — the one model with no `club` FK** (§2.4). Because a
user may belong to several clubs, `get_full_name` / `get_short_name` resolve the `Member`
**for the current club** (via tenant context), falling back to email — they can no longer
assume a single member.

### `members` *(planned — extract from `authentication`)*

`Member`, `Family`, `FamilyMembership` **move here** and become tenant-scoped
(`ClubScopedModel`).

**`Member`** — a *person within one club*. `user` becomes a **`ForeignKey`** (was
`OneToOneField`), still nullable (`on_delete=SET_NULL`), so one login maps to one member
*per club* (`unique_together (club, user)`) and members can exist without logins (children,
imports). Holds name, DOB, contact `email`/`phone`/`emergency_phone`. `contact_email`
prefers the member's own email, else the login email. `guardians` returns parent/guardian
members reachable through shared families (all within the same club).

**`Family`** + **`FamilyMembership`** — households, tenant-scoped. `FamilyMembership` carries
a `FamilyRole` (`parent` / `child` / `guardian` / `other`), `unique_together (family,
member)`; `Family.guardians` / `Family.children` are role-derived querysets. Powers the
"parents see their children's data" object-scope (§3.1).

**`Group`** *(built)* + **`GroupMembership`** — a generic, tenant-scoped, **opaque** named
collection of members: "all coaches", "all team managers", an ad-hoc committee. Deliberately
minimal (`name` + a through-membership, same shape as `Family`/`FamilyMembership`) — it
carries **no knowledge of any specific consumer** (not team-scoped, not referee-scoped, not
anything-scoped). Any feature wanting to use "a named set of people" for something specific
builds its own connective model elsewhere rather than teaching `Group` about that use case —
see `teams.RefereeProfile` (§5.2), which deliberately does **not** go through `Group` even
though an earlier draft of that feature did; referee eligibility is a fact about a *member*,
not about group membership.

```
Group(ClubScopedModel)                # -> carries `club`
  name  CharField
  Meta: UniqueConstraint(club, name)

GroupMembership(UUIDModel)            # club implied by group
  group   FK Group (CASCADE, related_name="memberships")
  member  FK Member (CASCADE, related_name="group_memberships")
  Meta: UniqueConstraint(group, member)
```

### `club`

**`Club`** — **the tenant root** (§2.4). Currently just `name`; extend with `slug` (unique,
drives subdomain/path resolution), contact, and branding. Provide `Club.objects.current()`
and resolve it in middleware — never hardcode a PK.

**`ClubMembership`** — links a `Member` to the `Club` with a `license` string. **Being made
season-scoped** (§5.1): it gains a `season` FK and sign-up / fee-status fields, so each row
is one member's affiliation for one season (`unique_together (club, member, season)`). This
is the record the `MEMBER` role and shop fulfilment key off of (§3.4, §5.7).

### `billing` — what the platform charges a club

**Deliberately NOT club-scoped, and the only app that isn't.** `shop` (§5.7) is a club charging
its *members* — tenant data, owned by the club. `billing` is RosterChief charging the *club*:
platform-owned, never visible to a club user except as the one notice described below. Nothing
here inherits `ClubScopedModel` — these rows reference a `Club`, they are not owned by one, and a
tenant-scoped manager would be exactly the wrong default.

**`Plan`** — a duration and three clocks, named for what they measure *from*, which is the easy
thing to get wrong: `duration_months` (period length, from its start), `renewal_lead_days` (how far
*before* a period starts its invoice is raised), `grace_days` (how long *after* a period starts it
may stay unpaid). Two `CheckConstraint`s keep them coherent. `is_trial` marks a plan offered as a
trial; a trial's length is simply its own `duration_months`. `deleted_at` is a soft-delete marker:
`Due.plan` is `PROTECT`, so a plan that has ever billed anyone can't really be removed — "delete"
hides it (`Plan.objects.visible()` excludes it) and unsubscribes every club currently on it instead;
see `billing/services/plans.py` and `BILLING.md` §11.

**`PlanPrice`** — a dated price (`active_from`). A rate change is a new row, never an edit, so
every period already opened keeps what it was billed at.

**`Subscription`** — one per club (`OneToOneField`): its current `plan`, `auto_renew`,
`auto_archive`, and the trial pair (`trial_ends_at` + `post_trial_plan`, constrained to be set
together or not at all).

**`Due`** — one billing period for one club, and **the snapshot boundary**. `plan`, `amount`,
`period_end` and `grace_until` are all frozen when the period opens and never read back through
the plan at display time: raise a price or edit a plan's grace and last year's invoice must still
say what was actually charged. Storing the computed *dates* rather than the plan's *numbers* is
what buys that.

**`DuePayment`** / **`Invoice`** — money received against a due (several may land on one), and the
gapless per-year invoice number. The PDF itself is rendered on demand from the `Due` snapshot;
only the number is stored.

All lifecycle changes go through `billing/services/` — `dues.py` (open, renew, pay, waive,
archive), `notices.py` (the one club-facing warning), `reminders.py` (its email). Never through
the models directly: a `Due` whose `amount_paid` disagrees with its payments is a wrong invoice.

**`BILLING.md` is the authoritative document for this app** — the lifecycle, the worked timelines,
and the migration hazards live there rather than here.

---

## 5. Planned models (design)

Field lists below are **sketches** to implement against, not final migrations.

All sketches below are tenant-scoped: aggregate roots inherit **`ClubScopedModel`** (the
`club` FK is shown implicitly and every listed `unique_together` is *within a club*, §2.4).

### 5.1 `Season` — the central organizing concept  *(app: `club`)*

Everything time-bound hangs off a season. Rosters, events, and attendance are
**season-scoped via FK** — never global state. Seasons are **per club** — each club runs
its own.

```
Season(ClubScopedModel)              # -> carries `club`
  name        CharField           # e.g. "2025–2026"
  start_date  DateField
  end_date    DateField
  is_current  BooleanField        # exactly one true PER CLUB; enforce in save()/service
  Meta: unique_together (club, name); ordering = ["-start_date"]; get_latest_by = "start_date"
```

- Provide `Season.objects.current()` (scoped to the current club, §2.4) rather than
  scattering `is_current=True` filters.
- Referenced by `Team`, `Event`, and `ClubMembership`. Use `on_delete=PROTECT` on those
  FKs — deleting a season with data should be blocked.

**Season-scoped `ClubMembership`** (evolution of the built model, §4):

```
ClubMembership(ClubScopedModel)      # -> carries `club`
  member       FK Member (CASCADE, related_name="club_memberships")
  season       FK Season (PROTECT, related_name="memberships")
  license      CharField (blank)        # federation license for that season
  status       CharField (TextChoices: pending | active | lapsed | cancelled)
  fee_status   CharField (TextChoices: unpaid | partial | paid | waived)
  signed_up_at DateTimeField (null)     # when the member registered for the season
  activated_at DateTimeField (null)     # when membership became active (usually on payment)
  Meta: unique_together (club, member, season); ordering = ["-season__start_date", ...]
```

- One row per member **per season** — sign-up and fee payment are tracked independently
  each season. `unique_together` moves from `(club, member)` → `(club, member, season)`
  (a data migration must backfill existing rows with the current season).
- `fee_status` is the **source of truth for whether dues are paid**; it is driven by the
  shop (§5.7) — a paid membership `Order` flips `fee_status → paid` and `status → active`.
  Keep it denormalized here (fast to query "who hasn't paid") but only mutate it through a
  service that reconciles against `Payment`s, never by hand.
- `status = active` (for the club's current season) is the fact that grants the member's
  user the `MEMBER` `ClubRole` (§3.4).

### 5.2 `teams`

```
Team(ClubScopedModel)              # -> carries `club`
  season      FK Season (PROTECT, related_name="teams")
  name        CharField           # "U12 A"
  age_group   CharField (choices, optional)
  Meta: unique_together (season, name); ordering = ["season", "name"]

TeamMembership(UUIDModel)          # roster entry — through model, club/season implied by team
  team        FK Team (CASCADE, related_name="roster")
  member      FK Member (CASCADE, related_name="team_memberships")
  position    CharField (TextChoices, optional)
  jersey_number PositiveSmallIntegerField (null=True)
  Meta: unique_together (team, member);
        UniqueConstraint(team, jersey_number) WHERE jersey_number IS NOT NULL

StaffAssignment(UUIDModel)         # coach / manager on a team, per season
  team        FK Team (CASCADE, related_name="staff")
  member      FK Member (CASCADE, related_name="staff_assignments")
  role        CharField (TextChoices: coach | assistant | manager)
  Meta: unique_together (team, member, role)
```

A `Member` plays on one *or more* `Team`s per season, each with its own position + jersey
number — modeled by `TeamMembership`, exactly matching the domain note.

- **Jersey numbers are unique within a team** (decision §7 #4): a partial
  `UniqueConstraint(fields=["team", "jersey_number"], condition=~Q(jersey_number=None))`.
  Nullable so a roster spot can exist before a number is assigned; `NULL`s are exempted so
  several unnumbered entries don't collide. `team` already implies club + season, so no
  extra tenancy field is needed on the constraint.
- `StaffAssignment` drives the coach/manager object-scope (§3.1–3.2) — it *is* the "is a
  coach of this team" fact; no `ClubRole` mirrors it.

**As built, `Team` also carries `referee_management`** (`TextChoices`: `club` | `federation`,
default `club`) — whether the *club* arranges referees for this team's home games, or the
*federation* does. A federation-managed team is left out of the referee tools **entirely**:
no eligibility, no assignment, no entry on the referee management dashboard (§5.3) — see
`events/services/referees.py::needs_referee_management(event)`, the single gate every
referee-facing screen reads through.

**`RefereeLevel`** *(built)* — a club-defined referee qualification tier ("Regional",
"National", ...), admin-managed like `Position` (own name, own ordering, no fixed list).
**Owns which teams it qualifies for** — eligibility is a property of the *level*, not of the
individual referee: a club configures a handful of levels once, each unlocking a tier of
teams, rather than hand-picking teams per referee.

```
RefereeLevel(ClubScopedModel)      # -> carries `club`
  name      CharField
  ordering  PositiveSmallIntegerField (default=0)
  teams     M2M Team (blank=True, related_name="referee_levels")
  Meta: UniqueConstraint(club, name); ordering = ["ordering", "name"]
```

**`RefereeProfile`** *(built)* — a **member-level** fact: which level this member holds and
how long it's valid for. Which teams that translates to is *derived* (`eligible_teams`),
never picked per member. Managed from the member's own page (`management`), read (not
edited) from the team's own page too. Deliberately **not** routed through `members.Group` —
eligibility is a property of a person, not of a group they might belong to; see the note on
`Group` above for why an earlier draft that did this was reworked. It also deliberately does
**not** put `teams` directly on the profile — a later draft of this feature did that too,
before the levels-own-the-teams shape replaced it, matching how real officiating
qualifications actually work (a certification tier unlocks a tier of competitions).

```
RefereeProfile(UUIDModel)          # club reachable via member -- Member itself has no club FK
  member       OneToOneField members.Member (CASCADE, related_name="referee_profile")
  level        FK RefereeLevel (PROTECT, null=True, blank=True, related_name="referees")
  valid_until  DateField (null=True, blank=True)
```

- **`is_currently_valid`** (property): `valid_until` is set and hasn't passed — a pure date
  check, independent of whether a level is even set.
- **`is_eligible`** (property): the full gate every consumer reads through (the event assign
  panel, the team page, the referees list) — `level` is set **and** `is_currently_valid`.
  Once `valid_until` passes, `is_eligible` flips to `False` and the referee drops out of
  every eligibility query until the date is extended; nothing else needs to change.
- **`eligible_teams`** (property): `level.teams.all()` when `is_eligible`, else empty.
- One `RefereeProfile` per member (`OneToOneField`) rather than a field bag on `Member`
  itself, matching this file's general pattern of keeping `Member` a plain identity record
  and hanging every role-specific fact off its own small table (`ClubMembership`,
  `StaffAssignment`, `TeamMembership`, and now this).

### 5.3 `events`

```
Event(ClubScopedModel)             # -> carries `club`
  season      FK Season (PROTECT, related_name="events")
  team        FK Team (SET_NULL, null=True, related_name="events")   # null = club-wide
  kind        CharField (TextChoices: training | match | tournament | social | meeting)
  title       CharField
  location    CharField (blank)
  starts_at   DateTimeField
  ends_at     DateTimeField (null=True)
  opponent    CharField (blank)    # for matches
  Meta: ordering = ["starts_at"]

Attendance(UUIDModel)              # through model Event <-> Member
  event       FK Event (CASCADE, related_name="attendances")
  member      FK Member (CASCADE, related_name="attendances")
  status      CharField (TextChoices: present | absent | excused | maybe)
  note        CharField (blank)
  Meta: unique_together (event, member)
```

`Event.season` is redundant with `team.season` when a team is set, but events can be
club-wide (`team=None`), so `season` stays a first-class FK. Keep it consistent in a
service/clean().

**As built, `Attendance` also carries `showed_up`** (nullable bool, default `None`) —
deliberately separate from `status`: `status` is the RSVP, `showed_up` is whether they
actually turned up, set by a check-in. `None` means "never checked in" (true for every
row today — there's no check-in UI yet, only Django admin); a "no-show" is
`status in (present, selected)` and `showed_up is False`, and is *never* inferred from
a missing check-in. See `events/services/attendance.py::record_check_in` and
`management/views.py::TeamDetailView`'s attendance panel.

**As built, a GAME-kind `Event` defaults its own `end`** — `Event.save()` sets
`end = start + events.models.ASSUMED_EVENT_DURATION` (2 hours) whenever a game is saved
with no explicit `end`, and never overwrites one that's already set. Other event kinds are
untouched — `end` stays blank for them unless explicitly given one. The public games API
(`events/api.py`, `GET /games/upcoming/`) reads through this: it returns every non-cancelled
game/tournament that **hasn't finished yet** (`end` — explicit, defaulted, or, for the rare
un-saved-since / non-GAME row still lacking one, `start` within the assumed window — is at or
after now), not just ones that haven't started, so a game already in progress keeps showing up
until its window closes; `GameOut.end` is always populated the same way, and `status` treats
"started but before its (assumed) end, not flagged `is_live`" as `"live"` too, so a game
`/games/upcoming/` still lists never turns around and calls itself `"finished"`.

**As built, `Event` also carries `max_referees`** (`PositiveSmallIntegerField`, default
`2`) and **`EventReferee`** *(built)* — referee sign-up/assignment for a **home game**
only (`Event.is_home_game`), staff-assigned for now (self-service subscribe is a planned
extension, §7). A referee row is either a club member **or** an externally-logged name
(e.g. a federation-appointed referee the club still needs to pay), never both/neither, and
carries its own payment snapshot:

```
EventReferee(UUIDModel)            # club implied by event
  event         FK Event (CASCADE, related_name="referees")
  member        FK Member (CASCADE, null=True, blank=True, related_name="referee_assignments")
  external_name CharField (blank=True)   # set instead of member for a non-member referee
  assigned_by   FK Member (SET_NULL, null=True, related_name="+")
  fee           DecimalField (default 0.00)
  km            DecimalField (null=True, blank=True)
  km_rate       DecimalField (null=True, blank=True)   # snapshotted per assignment, not a
                                                         # live club-wide setting
  Meta: unique_together (event, member); CheckConstraint XOR(member, external_name)
  display_name / is_external / km_total / total_payable   # computed properties
```

- **Eligibility** comes from `teams.RefereeProfile.is_eligible`/`eligible_teams` (§5.2): a
  member is eligible to referee an event if their profile is currently eligible (a level is
  set and its validity hasn't passed) and that level qualifies for one of the event's
  `teams`. `events/services/referees.py::eligible_referees(event)` computes this, and is
  empty for anything `needs_referee_management(event)` says no to — not a home game, or a
  home game whose team(s) are all federation-managed (§5.2). External referees bypass
  eligibility entirely (`add_external_referee`) — they're logged by name only, not vetted
  against a level.
- **Assignment is admin-only for now**, stricter than most event actions (a team
  manager/coach can edit the event itself, but not the referee panel's assign/remove/fee
  controls) — see `EventRefereeAssignView`/`EventRefereeRemoveView`/
  `EventRefereeAddExternalView`/`EventRefereeFeeUpdateView` (all `ClubAdminRequiredMixin`)
  and `EventDetailView`'s separate `can_manage_referees` flag. A team manager still **sees**
  the panel (who's assigned, capacity, fees) — visibility and authority are deliberately
  split here, same reasoning as §3's "coach visibility ≠ coach authority" for team rosters.
- **The referee management dashboard** (`management:referee_management`, admin-only) is the
  one-stop alternative to hunting through individual events: every upcoming home game
  `needs_referee_management`, with inline assign/remove/add-external/fee-editing (posting to
  the same views the event detail page uses, returning to the dashboard via a `next` param
  rather than the event detail page). It leads with KPI tiles (games in view, without a
  referee, partially staffed, fully staffed) and a button-based range filter (this
  week/this+next week/next 10/25/50 — an ISO-week window for the calendar options, a flat
  slice for the count ones), then lists games grouped by date as compact tiles; each tile's
  "Manage" button opens a `<dialog>` with the full assign/remove/external/fee panel so the
  list itself stays scannable. Both the dashboard and the event detail page share one
  `_referee_assignment_panel.html` include so this UI never drifts out of sync between them.
- **`max_referees` is a hard ceiling everywhere** — staff and external assignment included.
  Enforced in `_lock_and_check_capacity()` (shared by `assign_referee()` and
  `add_external_referee()`), which locks the `Event` row (`select_for_update`) for the
  duration of the count-check + write so two admins assigning at the same moment can't both
  squeeze past the ceiling.
- **Schedule conflicts are a soft warning, never a block.** `conflicting_events(member,
  event)` finds other events overlapping this one's time window where the member is part of
  the expected audience (`effective_members`, reused from the attendance service above) — the
  UI shows it (⚠ + tooltip on the assign control) but a human decides; an event with no
  explicit `end` is assumed to run `events.models.ASSUMED_EVENT_DURATION` (2 hours) for this
  check. External referees have no conflict check (no member to check a schedule against).
  `ASSUMED_EVENT_DURATION` is also what `Event.save()` writes into `end` for a GAME with none
  set (below) — the *other* event kinds still leave `end` blank rather than defaulting it, so
  this read-time fallback still matters for them.
- **`assigned_by` is required for now** (admin-only assignment). A future self-service
  sign-up would make it nullable to mean "the referee signed themself up" rather than adding
  a parallel model — see §7.
- **The referee payment form is a downloadable PDF** (`event_referee_form_pdf`,
  `EventRefereeFormPdfView`, admin-only, WeasyPrint via `management/pdf.py`'s lazy-import
  pattern), modeled directly on the club's existing paper form: game details, referee names,
  a fee+km breakdown per referee, and blank signature lines (referee always; team manager
  left blank — not reliably known at print time). The header uses `Club.official_name`
  (`legal_name` if the club has set one, else plain `name` — §2.2) and the club's home
  `Location` address; the body's payment sentence uses the plain `name` — mirroring the
  original paper form, which itself uses a longer legal form up top and a shorter one in the
  body text.

### 5.4 `news`, `pages`, `home` (public site / editorial)

**`news` is built** (as of the coach_manager-authoring / editor-release-flow work) —
team-tagged instead of categorised, with a two-step release flow rather than a bare
`is_published` flag:

```
news.News(ClubScopedModel)         # -> carries `club`
  title, slug (SlugField, auto from title), body (TextField)
  title_en    CharField (blank) -- optional English translation of `title`
  body_en     TextField (blank) -- optional English translation of `body`
  teams       M2M teams.Team (blank -- empty means club-wide)
  visibility  CharField (TextChoices: internal | external | both)
  status      CharField (TextChoices: draft | published)
  published_at DateTimeField (null) -- may be in the future: a *scheduled* release,
                                       not a cron-flipped field (see below)
  created_by  FK members.Member (SET_NULL, null, related_name="news_items")
  Meta: unique_together (club, slug); ordering = ["-created"]

news.NewsPhoto(UUIDModel)          # club reached via news_item, not directly scoped
  news_item   FK news.News (CASCADE, related_name="photos")
  image       ImageField
  is_main     BooleanField
  ordering    PositiveSmallIntegerField
  Meta: UniqueConstraint(fields=["news_item"], condition=Q(is_main=True))
        -- a partial unique index enforcing "at most one main photo per item"
        at the DB level, the same trick teams.Position uses for
        management_position_implies_staff_position.

pages.Page(ClubScopedModel)        # flat CMS pages: "About", "Contact", ...
  title, slug, body (TextField)
  is_published BooleanField; menu_order (int)
  Meta: unique_together (club, slug)
  # If nested navigation is needed, add: parent = FK self (SET_NULL, null)

home.HomeConfig(ClubScopedModel)   # one row PER CLUB: featured articles/teams, hero content
                                   # (unique_together (club,) — one per tenant). May be config-only.
```

- **Authoring vs. releasing are deliberately separate authorities**
  (`club/services/access.py::can_add_news`/`can_publish_news`/`can_edit_news`): any
  current-season coach_manager (management-position `StaffAssignment`), EDITOR, or
  ADMIN can draft a `News` item and edit it while it's a draft; only EDITOR/ADMIN can
  move it to `published` (or edit it once it is) — a physio or plain staff member can't
  post news, and a coach_manager can't push their own draft live.
- **Scheduling needs no cron job.** `published_at` can be set in the future; `status`
  already reads `PUBLISHED` (it passed the editor's release gate) but `News.is_scheduled`
  is true until that moment passes. A later public/member-facing consumer just filters
  `status=PUBLISHED, published_at__lte=now()` — nothing has to flip a row at the
  scheduled instant.
- **`created_by` links to `members.Member`** (decision §7 #5) — attribution is to a
  club person, not a raw login; `SET_NULL` so deleting a member doesn't erase their posts.
- `slug`s back clean public URLs and feed `search`; they are **unique per club** (§2.4), so
  two clubs can both have `/news/season-kickoff`. Resolve within the request's club.
- `visibility` (internal/external/both) is enforced by the public read-only API
  (`news/api.py`, mounted under `api/`) — only `external`/`both` items, published and
  past their release date, are ever returned. No member-facing internal reading page
  exists yet; that's later work.
- **`title`/`body` are Dutch (the club's own language, and the only one required);
  `title_en`/`body_en` are an optional English translation**, both left blank by
  default. Nothing computes or stores a fallback — `News.effective_title_en` /
  `effective_body_en` resolve it on read (`title_en or title`), so translating a Dutch
  edit later never leaves a stale English copy behind, and every existing row gets
  correct fallback behaviour with no backfill. The public API always returns both
  languages in one call (`title_nl`/`body_nl`/`excerpt_nl` alongside
  `title_en`/`body_en`/`excerpt_en`, the latter three via the `effective_*` properties
  so they're never blank) — no `?lang=` param, the consumer picks what it needs. The
  control panel's news form lays the two languages out in side-by-side columns
  (`management/templates/management/news_form.html`); the detail page only shows an
  "English" section when a translation was actually added, not the fallback-filled
  text under a second heading.
- `NewsPhoto.image` / hero images use `ImageField` → **media storage must be configured**
  (§8). If page/news trees grow, consider a tree library later — start flat.

### 5.5 `search`

Likely **no models** — a search view over `Article`, `Page`, `Team`, `Event`. If moving to
Postgres full-text or an external index, add config here, not domain tables.

### 5.6 `formbuilder` — dynamic forms  *(new app)*

Admins/editors define forms with a **variable number of fields** at runtime; submissions
are stored so they can be **reported on** later. This uses the classic EAV (entity-
attribute-value) shape with **normalized `Answer` rows as the single source of truth**
(decision §7 #9) — one row per answered field, with a JSON `value` to stay flexible across
field types. No parallel JSON blob on the submission — reporting reads `Answer`s directly.

```
Form(ClubScopedModel)                 # -> carries `club`
  title, slug, description (blank)
  is_active      BooleanField
  login_required BooleanField        # members-only vs public submission
  opens_at / closes_at  DateTimeField (null)   # optional submission window
  max_submissions_per_user  PositiveInteger (null)   # null = unlimited
  Meta: unique_together (club, slug); ordering = ["title"]

Field(UUIDModel)                      # a form's field definition (club implied by form)
  form       FK Form (CASCADE, related_name="fields")
  key        SlugField               # stable machine name, unique per form (for reporting)
  label      CharField
  field_type CharField (TextChoices: text | textarea | number | email | date |
                                     choice | multichoice | checkbox | file)
  required   BooleanField
  help_text  CharField (blank)
  order      PositiveSmallIntegerField
  is_active  BooleanField (default=True)   # soft-retire instead of deleting (see below)
  options    JSONField (default=list) # choices for choice/multichoice: [{value,label}]
  Meta: unique_together (form, key); ordering = ["form", "order"]

Submission(UUIDModel)                 # container only — no answer data on it
  form         FK Form (CASCADE, related_name="submissions")
  member       FK Member (SET_NULL, null)   # set when submitter is logged in
  submitted_at DateTimeField
  Meta: ordering = ["-submitted_at"]

Answer(UUIDModel)                     # CANONICAL store — one per answered field
  submission FK Submission (CASCADE, related_name="answers")
  field      FK Field (PROTECT, related_name="answers")
  value      JSONField                # scalar, list (multichoice), or file ref
  Meta: unique_together (submission, field)
```

Design notes:
- **`Answer` is canonical; there is no denormalized JSON snapshot.** A submission's values
  are always read/aggregated from its `Answer` rows. The submit service
  (`formbuilder/services/submit.py`) validates the dynamic form and writes the `Submission`
  + its `Answer`s in one transaction. (If a flat per-submission view ever becomes a
  hotspot, add a *derived, rebuildable* cache later — but the model stays the source.)
- **`Field.key` is immutable once submissions exist** — reporting joins on it. Deleting a
  field with answers is blocked (`PROTECT`); **retire via `is_active=False`** instead.
- **Reporting** = a service/view producing per-field aggregates (counts per choice,
  numeric averages, response rate) plus a wide CSV/Excel export (one column per field,
  one row per submission). No extra model needed; add a saved-report model later only if
  users need to persist report definitions.
- Rendering a `Form` to a Django form (and validating a submission) is a service concern —
  build the form class dynamically from `Field` rows; don't hand-write form classes.

### 5.7 `shop` — cart, orders, payments & PDF invoices  *(new app)*

A cart-like shop where a member (or parent) "buys" products — chiefly a **season
membership** — with payment-status tracking and generated invoices. Fulfilment of a
membership product writes back to the season-scoped `ClubMembership` (§5.1).

```
Product(ClubScopedModel)              # -> carries `club`
  name, slug, description (blank)
  kind        CharField (TextChoices: membership | event_fee | merchandise | donation)
  price       DecimalField(max_digits=8, decimal_places=2)   # list price
  season      FK Season (PROTECT, null)   # set for membership/event products
  is_active   BooleanField
  # early-bird / prompt-payment discount (§5.7.1) — per-product toggle + deadline
  early_bird_enabled    BooleanField (default=False)
  early_bird_deadline   DateField (null)                  # discount valid through this date (inclusive)
  early_bird_disc_type  CharField (TextChoices DiscountType: PERCENT | AMOUNT, blank)
  early_bird_disc_value DecimalField(max_digits=8, decimal_places=2, null)  # 0–100 if PERCENT, else € off unit
  Meta: unique_together (club, slug)
        CheckConstraint: early_bird_enabled ⇒ deadline, disc_type, disc_value all set
  # membership products fulfil into a ClubMembership for the chosen season + beneficiary

Cart(ClubScopedModel)                 # -> carries `club`; one open cart per (club, user)
  user        FK User (CASCADE, related_name="carts")
  status      CharField (TextChoices: open | checked_out | abandoned)
  Meta: UniqueConstraint(club, user) WHERE status = open

CartItem(UUIDModel)                   # club implied by cart
  cart        FK Cart (CASCADE, related_name="items")
  product     FK Product (PROTECT)
  beneficiary FK Member (PROTECT, null)   # who this membership is FOR (parent buys for child)
  quantity    PositiveSmallIntegerField (default=1)
  unit_price  DecimalField               # snapshot of price at add-to-cart time
  Meta: unique_together (cart, product, beneficiary)

Order(ClubScopedModel)                # -> carries `club`; created `pending` at checkout,
                                      # frozen at finalize() (§5.7.1 lifecycle)
  number      CharField                  # human ref, allocated PER CLUB, e.g. "ORD-2026-00042"
  purchaser   FK Member (PROTECT, related_name="orders")
  status      CharField (TextChoices: pending | finalized | paid | partially_paid | cancelled | refunded)
  subtotal    DecimalField               # Σ OrderLine.line_total (after per-line early-bird)
  # order-level discounts are SELECTED from a club catalogue, not typed — see AppliedDiscount
  # below + OrderDiscountType (§5.7.1); applied by a treasurer while status=pending.
  total       DecimalField               # subtotal - Σ applied discounts; the amount invoiced
  created_at  DateTimeField
  finalized_at DateTimeField (null)
  Meta: unique_together (club, number); ordering = ["-created_at"]

OrderLine(UUIDModel)                  # club implied by order
  order       FK Order (CASCADE, related_name="lines")
  product     FK Product (PROTECT)
  beneficiary FK Member (PROTECT, null)
  quantity    PositiveSmallIntegerField
  list_price  DecimalField               # catalogue unit price at checkout (snapshot)
  unit_price  DecimalField               # price actually charged after per-line early-bird (snapshot)
  discount_label CharField (blank)        # e.g. "Early bird (−15%)" — shown on invoice; blank = none
  line_total  DecimalField               # unit_price * quantity
  fulfilled_at DateTimeField (null)       # when this line's ClubMembership was activated

Payment(UUIDModel)                    # club implied by order; an order may have several (partial)
  order       FK Order (CASCADE, related_name="payments")
  amount      DecimalField
  method      CharField (TextChoices: bank_transfer | cash | card | online)
  status      CharField (TextChoices: pending | confirmed | failed | refunded)
  reference   CharField (blank)          # bank/gateway reference
  paid_at     DateTimeField (null)

OrderDiscountType(ClubScopedModel)    # -> carries `club`; club-defined catalogue of presets
  name        CharField                  # "Sibling discount", "Volunteer", "Hardship"
  slug        SlugField
  disc_type   CharField (TextChoices DiscountType: PERCENT | AMOUNT)
  value       DecimalField(max_digits=8, decimal_places=2)   # 0–100 if PERCENT, else € off subtotal
  description CharField (blank)          # optional note shown to the treasurer
  is_active   BooleanField (default=True)   # soft-retire; keeps historical AppliedDiscounts valid
  Meta: unique_together (club, slug); ordering = ["name"]

AppliedDiscount(UUIDModel)            # through: Order <-> OrderDiscountType; club implied by order
  order         FK Order (CASCADE, related_name="discounts")
  discount_type FK OrderDiscountType (PROTECT, related_name="applications")
  # snapshot at apply time — the preset may be edited/retired later without altering past orders
  label       CharField                  # snapshot of name (shown on invoice)
  disc_type   CharField (PERCENT | AMOUNT)   # snapshot
  value       DecimalField               # snapshot (or a treasurer override, if allowed)
  applied_by  FK User (SET_NULL, null, related_name="+")
  applied_at  DateTimeField
  Meta: unique_together (order, discount_type)   # a preset toggles on/off once per order

Invoice(ClubScopedModel)              # -> carries `club`
  number      CharField                  # sequential PER CLUB per year, e.g. "INV-2026-00042"
  order       OneToOneField Order (PROTECT, related_name="invoice")
  issued_at   DateTimeField
  due_date    DateField (null)
  billing_snapshot JSONField             # name/address frozen at issue time
  pdf         FileField (null)           # rendered HTML->PDF, cached in PRIVATE storage (§8)
  Meta: unique_together (club, number)
```

Flow & design notes:
- **Cart → checkout → order.** Checkout converts the open `Cart` into an immutable `Order`
  + `OrderLine`s, snapshotting prices (products may reprice later). The cart is marked
  `checked_out`. All in one transactional service (`shop/services/checkout.py`).
- **Payment status is derived, not typed by hand.** A service sums `confirmed` `Payment`s
  and sets `Order.status` (`pending` → `partially_paid` → `paid`). Online payments arrive
  via a gateway webhook that creates/confirms a `Payment`; manual methods
  (bank transfer/cash) are confirmed by a `TREASURER` (§3.2).
- **Fulfilment writes back to membership.** When an order (or a membership line) reaches
  `paid`, a service creates/activates the `ClubMembership(member=beneficiary, season=…)`,
  flips its `fee_status → paid` / `status → active`, stamps `OrderLine.fulfilled_at`, and
  triggers role reconciliation (§3.4). This is the seam that ties the shop to the domain.
- **Beneficiary vs. purchaser** is first-class: a parent (`purchaser`) buys memberships for
  several children (`beneficiary`) in one order. Both are `Member`s of the same club.
- **Invoice = HTML → PDF.** Render a Django template to HTML, convert with **WeasyPrint**
  (see §8 for the exact dependency + native-library setup). Generate on order confirmation,
  store the file on `Invoice.pdf` in **private** storage, and serve it only through a
  permission-checked view (never a public media URL — invoices are tenant-private, §8).
  `Invoice.number` uses a gap-free counter **per club per year** — allocate it in a
  transaction/service (e.g. a `select_for_update` sequence row), not from `count()`.
- **Money = `DecimalField`**, never float. Snapshot prices onto cart items / order lines /
  invoices so historical records stay correct when `Product.price` changes.

#### 5.7.1 Discounts

Two independent discount mechanisms, applied at different layers and computed by a single
**pricing service** (`shop/services/pricing.py`) so the rules live in one place and never
in views/templates. A shared `DiscountType` enum (`PERCENT` / `AMOUNT`) is reused by both.

**A. Early-bird / prompt-payment discount — per `Product`, automatic.**
A club toggles `early_bird_enabled` on a product, sets an `early_bird_deadline`, and a
`PERCENT` or `AMOUNT` value (§5.7 `Product`). Semantics: *buy in time and the unit price
drops.*
- **Anchor = checkout date (recommended).** The discount is evaluated **once, at checkout**,
  comparing the order's `created_at` date against the deadline, and the result is frozen into
  `OrderLine.unit_price` (+ a human `discount_label`, with `list_price` preserved for
  transparency). This keeps the order/invoice total firm — an invoice can't have a
  conditional amount. To still reward *paying* early, set the membership `Invoice.due_date`
  to the deadline; late non-payment is a dunning concern, not a repricing one.
- **Alternative (payment-date anchor)** — the discount only sticks if a confirmed `Payment`
  lands by the deadline, else the line reprices to `list_price`. This makes the total mutable
  until the deadline and complicates invoicing; it's the literal reading of "paid before
  date" but is deferred unless a club needs it (see open question, §7).
- Only applies when `today <= early_bird_deadline`; otherwise the line charges `list_price`.
  A `CheckConstraint` guarantees an enabled product has a deadline + type + value.

**B. Order-level discount — selected from a club catalogue of presets.**
Rather than typing a type + value + reason per order, each club **defines named presets once**
as `OrderDiscountType` rows (e.g. *Sibling discount −15%*, *Volunteer −€25*, *Hardship*),
managed under the club's shop settings. On a `pending` order a `TREASURER`/`BOARD` (§3.2)
simply **toggles the applicable presets on** — each toggle creates an `AppliedDiscount` row.
No arithmetic is entered at order time; the treasurer picks from a list.
- **Snapshot, like prices.** `AppliedDiscount` copies the preset's `label` / `disc_type` /
  `value` at apply time. Editing or retiring (`is_active=False`) an `OrderDiscountType` later
  never rewrites past orders — historical totals stay correct. `PROTECT` on the FK means a
  used preset can't be hard-deleted; retire it instead.
- **Multiple discounts stack** — several presets can apply to one order (a preset toggles on
  at most once, via `unique_together (order, discount_type)`). See stacking rule below.
- **Optional override.** The default flow enters *zero* numbers. If a club needs a one-off
  amount (e.g. a bespoke hardship figure), allow the treasurer to override the snapshot
  `value` on that `AppliedDiscount` — an opt-in escape hatch, not the primary path. A pure
  ad-hoc discount is then just a generic "Custom" preset with an overridden value.
- **Lifecycle refinement.** Discounts force the order to be *editable before it freezes*:
  checkout creates the order as **`pending`**; a treasurer toggles presets on/off while
  `pending`; `finalize()` then locks the order + its `AppliedDiscount`s, computes the final
  `total`, allocates the `Invoice.number`, and issues the PDF. **After `finalize` everything
  is immutable** — a correction means a credit/refund, not an edit. Gated by
  `can_manage_shop(user, club)`.

**Computation & rounding (both kinds).**
`total = subtotal − Σ applied_discounts`, where `subtotal = Σ line_total` and each
`line_total` already reflects the early-bird price. Order of application: **line-level
early-bird first, then all order-level presets**, each computed against the **same
`subtotal` base** (percentages don't compound on each other — predictable and order-
independent) and summed. Percentages compute on their base, round **`ROUND_HALF_UP` to 2
decimals**; the **summed** order discount is **clamped to `[0, subtotal]`** so an order can
never go negative. Every discounted document (order summary, invoice) itemises each applied
discount by `label` plus the net so members see how the number was reached.

**Extension point.** `OrderDiscountType` is the reusable catalogue for the two required cases.
Coupon *codes* (member-entered), auto-applied promotions (rule-based, e.g. "3+ siblings"), or
per-member entitlements would extend this — add an eligibility rule / code field or an
auto-apply service on top of the same model when that need is real, rather than a parallel
mechanism.

---

## 6. Entity-relationship overview

```
Club  (TENANT ROOT — every model below except User carries `club`; §2.4)
  └─< Season, Member, Family, Team, Event, ClubRole, Article, Page, Form, Product, Order, Invoice, …

User 1───<  Member  (FK, unique per club)      # User is GLOBAL — no club FK
              │  └───< FamilyMembership >─── Family
              │  └───< ClubRole            (MEMBER | EDITOR | TREASURER | BOARD)
              │
              ├───< ClubMembership ──> Season        (unique: club, member, season)
              │
              ├───< TeamMembership >─── Team ───> Season
              ├───< StaffAssignment >─── Team          (= "coach of this team", §3.2)
              ├───< GroupMembership >─── Group          (opaque -- no team/referee link)
              ├─1:1─ RefereeProfile ──> RefereeLevel >──< Team
              │                         (profile's valid_until gates eligibility; level owns teams)
              │
              ├───< Attendance >─── Event ───> Season
              │                      └───> Team (nullable)
              ├───< EventReferee >─── Event     (assigned_by another Member; home games only)
              │
              ├───< Submission >─── Form ───< Field    (Submission ──< Answer >── Field)
              │
              └── (purchaser) ──< Order ───< OrderLine >── Product ──> Season
                                  │            └──> Member (beneficiary)
                                  ├──< Payment
                                  ├──< AppliedDiscount >── OrderDiscountType (club preset)
                                  └─1:1─ Invoice   (Cart ──< CartItem >── Product)

Season ──< Team, Event, ClubMembership, (membership/event) Product   # all within one club

news.Article ──> news.Category,  (author) members.Member
pages.Page  (self-parent, optional)
```

Legend: `───<` one-to-many, `>───<` many-to-many via a through model. Everything under
`Club` is one tenant's data; joins never cross clubs (§2.4).

---

## 7. Decisions (resolved) & remaining questions

**Resolved** (this revision):

1. ✅ **`members` app split** — Member/Family/FamilyMembership **move to a dedicated
   `members` app** (§1, §4). Migration reshuffles app labels + tables.
2. ✅ **Season-scoped `ClubMembership`** — yes (§5.1). Data migration backfills existing
   rows with the current season and re-scopes `unique_together`.
3. ✅ **Full multi-tenancy** — adopt **row-based multi-tenancy**, `Club` as tenant root
   (§2.4). Requires: `ClubScopedModel` on every aggregate root, `Member.user` →
   `ForeignKey` (+`unique(club, user)`), tenant middleware + `rosterchief/tenancy.py`
   context, tenant-aware manager, per-club uniqueness, per-club roles (§3). **Supersedes
   `CLAUDE.md`.**
4. ✅ **Jersey uniqueness** — unique **within a team** via a partial `UniqueConstraint`
   (`NULL`s exempt) (§5.2).
5. ✅ **`Article.author`** — links to `members.Member` (§5.4).
6. ✅ **RBAC mechanism** — **service layer**, no `django-guardian`; per-club `ClubRole`
   rows + a single access service (§3). Django's own perms only for platform admin.
7. ✅ **`formbuilder` storage** — **normalized `Answer` is canonical**; no denormalized JSON
   snapshot (§5.6).
8. ✅ **Tenant resolution ≠ `django.contrib.sites`** — Sites evaluated and rejected as the
   mechanism; `Club` stays the single tenant root, resolution in `ClubTenantMiddleware`
   (§2.4). Sites optional only as a later bridge for Site-aware third parties.
9. ✅ **Shop discounts** — two mechanisms (§5.7.1): a per-`Product` early-bird discount
   (toggle + deadline + PERCENT/AMOUNT, frozen at checkout) and **order-level discounts
   selected from a club catalogue of `OrderDiscountType` presets** — a treasurer toggles
   presets on a `pending` order (each = a snapshotting `AppliedDiscount` row) before
   `finalize()`, rather than typing values. Presets stack against the same subtotal base;
   optional per-row value override for one-offs. Adds an `Order.pending → finalized` step.

Infrastructure/config for the above (media storage, dependencies + exact setup) is
specified in **§8**.

**Still open:**

- **Tenant resolution mechanism** — subdomain (recommended) vs. path-prefix `/c/<slug>/`.
  Affects DNS/TLS, `ALLOWED_HOSTS`, cookies, and local dev (§8). Pick before building
  `ClubTenantMiddleware`. **`django.contrib.sites` was evaluated and rejected as the
  mechanism** (§2.4) — `Club` stays the single tenant root; Sites is optional only as a
  bridge for Site-aware third parties.
- **Auto-scoping vs. explicit scoping** — should the tenant manager filter *automatically*
  from context, or stay explicit (`.for_club()` / `.current()`)? Doc currently recommends
  **explicit** (§2.4).
- **Cross-club users** — can one person be a `BOARD` member of several clubs, switching
  context in one session? The model allows it; confirm the UX (club switcher) is in scope.
- **Payment gateway** — which provider (Mollie / Stripe / none-yet)? Only needed when online
  payments go live (§8).
- **Early-bird anchor** — is the discount earned by *ordering* before the deadline
  (checkout-date anchor, recommended, frozen total) or by *paying* before it (payment-date
  anchor, mutable total)? Doc implements checkout-date; confirm no club needs the literal
  "paid before date" semantics (§5.7.1).
- **Referee self-service sign-up** — `EventReferee` (§5.3) is admin-assigned only for now
  (a team manager/coach can see the panel but not use it); a referee cannot yet subscribe
  themself to a game. Adding it later means making `assigned_by` nullable (null =
  self-subscribed) and a permission mixin scoping a referee to their own eligible games — no
  new model needed. Not built because this app has no self-service (member-facing) surface
  of any kind yet; the first one deserves its own pass rather than riding along here.

---

## 8. Infrastructure & configuration notes

Config required by the models above — settings + dependencies to add as each app lands.

### 8.1 Media & file storage (needed for `news`, `home`, `shop`, form file fields)

Two classes of files with **different exposure**:

- **Public media** — `Article.cover_image`, home hero images. Served from the normal media
  URL is fine.
- **Private, tenant-scoped files** — `Invoice.pdf` and `formbuilder` file uploads. These
  **must not** be publicly reachable. Serve them only through a permission-checked Django
  view (`X-Accel-Redirect`/`X-Sendfile` in prod), never a guessable public URL, and scope
  access to the file's club (§2.4).

Setup:
- **Dev:** `MEDIA_ROOT`/`MEDIA_URL` on local disk; private files under a non-served path.
- **Prod:** object storage (S3-compatible) via **`django-storages`** (add to deps) with
  **separate public and private buckets/backends** (Django 5.1+ `STORAGES` setting). Keep
  the private backend non-public and generate signed/short-lived URLs or stream via the view.
- Organise keys by club (e.g. `club/<club_id>/invoices/…`) so tenant data is easy to
  isolate, audit, and delete.

### 8.2 HTML → PDF invoices — WeasyPrint

- Add the dependency: `uv add weasyprint`.
- **Native libraries required** (WeasyPrint wraps Pango/Cairo) — install at the OS/image
  level, not via pip:
  - macOS (dev): `brew install pango gdk-pixbuf libffi` (Cairo/GLib come along).
  - Debian/Ubuntu (CI + prod image): `apt-get install libpango-1.0-0 libpangocairo-1.0-0
    libcairo2 libgdk-pixbuf-2.0-0 libffi-dev` (exact names per distro/WeasyPrint version).
  - Document these in the Dockerfile/CI so PDF rendering isn't a "works on my machine" trap.
- Render a Django template → HTML string → `weasyprint.HTML(string=…).write_pdf()`; store
  onto `Invoice.pdf` (private storage, §8.1). Generation is a service, ideally async/queued
  if volume grows.

### 8.3 Tenancy runtime config (needed once `ClubTenantMiddleware` lands)

- **Hosts:** wildcard `ALLOWED_HOSTS` for the chosen base domain (e.g. `.rosterchief.app`)
  if using subdomain resolution; add `DJANGO_ALLOWED_HOSTS` accordingly.
- **CSRF:** `CSRF_TRUSTED_ORIGINS` must cover the wildcard scheme+host set
  (`https://*.rosterchief.app`).
- **Cookies:** to share login across club subdomains, set `SESSION_COOKIE_DOMAIN` /
  `CSRF_COOKIE_DOMAIN` to the base domain; otherwise keep per-subdomain sessions
  (decide with the "cross-club users" question in §7).
- **Local dev:** map a wildcard to localhost (e.g. `*.localhost` resolves on most systems,
  or use `dnsmasq`) so subdomain resolution works without editing `/etc/hosts` per club.
- **Middleware order:** place `ClubTenantMiddleware` after `AuthenticationMiddleware`
  (needs `request.user` to fall back to a user's default club when no subdomain is present).

### 8.4 Optional dependencies

- **Payment gateway** (only if online payments): provider SDK (e.g. `mollie-api-python` or
  `stripe`) + webhook endpoint that creates/confirms `Payment`s (§5.7).
- **Excel export** for form reporting beyond CSV: `openpyxl`.

---

*Conventions cross-reference:* `rosterchief/base.py` (`UUIDModel`, `ClubScopedModel`),
`rosterchief/tenancy.py` (*to add* — tenant context/middleware, §2.4),
`authentication/managers.py` (`UserManager`), `authentication/services/` (service-layer
pattern).

---

> ### ⚠️ Banner: supersedes `CLAUDE.md`
>
> This architecture adopts **full multi-tenancy** (§2.4), which **directly contradicts**
> the current `CLAUDE.md` ("RosterChief is a **single-club** app … deliberately *not*
> multi-tenant — there is no `club_id` tenancy") and the project memory
> (`project_overview` — "Single-club (NOT multi-tenant)").
>
> **Action required** before/alongside implementation: update `CLAUDE.md` and the memory
> to describe RosterChief as a **multi-tenant platform (row-based, `Club` = tenant root)**.
> Until that is done, where the two disagree **this document is authoritative**.
