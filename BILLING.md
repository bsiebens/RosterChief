# Platform billing — design

How RosterChief charges a club, and what happens when a club doesn't pay.

This is the design for a **revision** of the billing subsystem, not a rewrite. `ARCHITECTURE.md`
covers the club-owned domain; billing is the other direction — the platform charging the tenant —
and has never been documented there. Once this design is implemented, §4 (the model) gets folded
into `ARCHITECTURE.md` and this file keeps the lifecycle and operational detail, the same split
`DEPLOYMENT.md` already has with the rest of the docs.

Status: **implemented.** The four decisions left open in §10 have been taken and are recorded
there.

---

## 1. What ships today, and what's actually wrong with it

The existing implementation (`billing/`) is structurally sound. Three things in particular are
worth keeping and are not up for renegotiation in this redesign:

- **Snapshot-on-`Due`.** `Due.tier` and `Due.amount` are frozen when the period opens and never
  read back through the plan at display time. Raise a price and last year's invoice still says what
  was actually charged. This is the single most important property in the subsystem — a live
  lookup would rewrite financial history.
- **Dated prices.** `TierPrice.active_from` means a rate change is one new row with a future date,
  not an edit to an existing one. Every period already opened keeps its amount.
- **Asymmetric commands.** `archive_overdue_clubs` reports by default and only acts with
  `--commit`; `renew_subscriptions` acts by default and only previews with `--dry-run`. The
  asymmetry is deliberate and correct: not archiving is a safe failure, not renewing is a silent
  revenue leak.

What's wrong is narrower than "the approach": **three hardcoded assumptions**, all in
`billing/models.py`.

| # | Today | Problem |
|---|---|---|
| 1 | `Due.save()` → `add_one_year(period_start)` | Every plan is annual. Duration is only overridable by passing an explicit `period_end`, which is the hack trials use. |
| 2 | `GRACE_DAYS = 45`, module constant | One grace period for every plan, editable only by deploying code. |
| 3 | `grace_until = period_end + GRACE_DAYS` | **The clock runs from the wrong end.** A club uses the entire unpaid year *and then* gets 45 days — roughly **410 days of unpaid use** before `archive_overdue_clubs` will touch it. |

Item 3 is the real defect. The requested behaviour — "unpaid → warned → archived after N days" —
isn't a tweak to the current rule, it's the opposite of it.

A fourth gap is in the UI rather than the model: the club-facing banner
(`management/views.py:89-93`) fires on *the period ending*, not on *money being owed*. A club
that owes money is told nothing.

---

## 2. Target lifecycle

```
                    ┌──────────────────────────────────────────┐
                    │                                          │
   [no plan] ──► TRIAL ──────► UNPAID ──► IN GRACE ──► OVERDUE ─┴─► ARCHIVED
                  │              │  ▲         │           │            │
                  │              │  │         │           │            │
                  └──────────────┘  │      payment     payment    reactivate
                   auto-converts    │         │           │       (+ new period)
                   on renewal       │         ▼           ▼            │
                                    │       PAID        PAID           │
                                    └──────────────────────────────────┘
                                              renewal opens
                                              the next period
```

**Trial.** A club starts on a trial plan (there can be several — a 1-month and a 3-month trial are
just two plans). It carries a price of 0, so it settles itself the moment it opens and can never
make a club archivable. When the trial period runs out, the next renewal converts the subscription
onto the pre-selected paid plan. This already works (`open_period`'s trial-conversion check) and
is kept as-is.

**Unpaid.** Every paid period — new or renewed — opens `UNPAID` with an invoice attached. From
that moment the club's admins see a warning on their management home page. This is the state the
whole redesign is about.

**In grace.** The period has started and is still unpaid. The warning escalates and gains a
countdown: *"Your club will be archived in N days."*

**Overdue.** Past `grace_until`. `archive_overdue_clubs` will now pick this club up — subject to
`Subscription.auto_archive`, which stays as the manual override for a club you're negotiating with.

**Archived.** `Club.archive()` sets `archived_at`. The subdomain stops resolving
(`ClubTenantMiddleware.get_club()` filters on `Club.objects.active()`), so the club is frozen
exactly as requested: no access, nothing destroyed. Reactivation is a deliberate platform-admin
action.

---

## 3. The three clocks

Every plan carries three numbers. Getting them confused is the easiest way to misread this design,
so they are named for what they measure from:

| Field | Measured from | Answers |
|---|---|---|
| `duration_months` | `period_start` | How long is a period? |
| `renewal_lead_days` | `period_start`, backwards | How early is the invoice raised? |
| `grace_days` | `period_start`, forwards | How long may it stay unpaid? |

**`grace_days` runs from the period start, not from the invoice and not from the period end.**
That is the decision this redesign turns on. It means the warning window is
`renewal_lead_days + grace_days` — the club is told before the period begins *and* gets a further
grace once it has, but is cut off partway into a period it never paid for.

### Worked timelines

```
ANNUAL      duration 12mo · lead 30d · grace 30d
            period 1 Jan 2027 – 31 Dec 2027

  2 Dec 26      1 Jan 27      31 Jan 27              31 Dec 27
     │             │              │                       │
  INVOICE     PERIOD STARTS   grace ends              PERIOD ENDS
     │─────────────│──────────────│
      30d warned    30d grace     └─► ARCHIVED 1 Feb 27 if unpaid
     └──────────── 60 days of warning ────────────┘


QUARTERLY   duration 3mo · lead 14d · grace 30d
            period 1 Jan 2027 – 31 Mar 2027

  18 Dec 26     1 Jan 27      31 Jan 27      31 Mar 27
     │             │              │              │
  INVOICE     PERIOD STARTS   grace ends    PERIOD ENDS
     └──────── 44 days of warning ───┘


MONTHLY     duration 1mo · lead 7d · grace 14d
            period 1 Jan 2027 – 31 Jan 2027

  25 Dec 26   1 Jan 27   15 Jan 27   31 Jan 27
     │           │           │           │
  INVOICE   PERIOD ST.  grace ends  PERIOD ENDS
     └─── 21 days of warning ─┘
```

### Why the lead time has to be per-plan

Today's single `RENEWAL_LEAD_DAYS = 30` is silently annual-only. On a 1-month plan it would issue
the next period **before the current one had started** — periods would run away from the calendar
within a couple of cycles. Any plan shorter than 30 days is broken by a global constant, which is
why `renewal_lead_days` moves onto the plan.

### Guardrails

Two invariants, enforced as `CheckConstraint`s. `duration_months × 28` is the conservative
lower bound on days in that many months, which keeps the check expressible in SQL:

- `renewal_lead_days < duration_months * 28` — or the next invoice precedes the current period.
- `grace_days <= duration_months * 28` — or a club can never be archived before its next period is
  issued, and unpaid periods silently stack.

---

## 4. Model changes

### 4.1 `Tier` → `Plan` (rename)

The code says `Tier`, every spec and screen says "plan". Renaming now, while only ~80 references
exist, is cheaper than carrying the mismatch. `TierPrice` → `PlanPrice` follows, as do the FK
field names (`Subscription.tier` → `plan`, `Subscription.post_trial_tier` → `post_trial_plan`,
`Due.tier` → `plan`, `PlanPrice.tier` → `plan`).

### 4.2 `Plan` — new fields

```python
class Plan(UUIDModel):
    name, slug, description, is_active          # unchanged

    duration_months    = PositiveSmallIntegerField(default=12,  validators=[MinValueValidator(1)])
    renewal_lead_days  = PositiveSmallIntegerField(default=30)
    grace_days         = PositiveSmallIntegerField(default=30)
    is_trial           = BooleanField(default=False)
```

`is_trial` is an explicit flag rather than "price == 0" — a genuinely free tier is not a trial, and
the two dropdowns on the trial form need to offer different sets of plans. It also lets the control
panel keep trial plans out of the normal "change plan" picker.

`price_on(day)` is unchanged.

### 4.3 `Due` — derivation changes, fields don't

```python
def save(self, *args, **kwargs):
    if not self.period_end:
        self.period_end = add_months(self.period_start, self.plan.duration_months) - timedelta(days=1)
    if not self.grace_until:
        self.grace_until = self.period_start + timedelta(days=self.plan.grace_days)
    super().save(*args, **kwargs)
```

`grace_until` stays a **stored** field. That is what makes it a snapshot: editing a plan's
`grace_days` afterwards must not move the archive date of a period that is already running, for the
same reason `amount` is frozen. Storing the computed *date* rather than the input *days* gets this
for free — no extra column needed.

The predicates change meaning even though only one changes shape:

```python
def is_in_grace(self, today=None):      # period running, unpaid, not yet archivable
    return self.is_owing and self.period_start <= today <= self.grace_until

def is_overdue(self, today=None):       # unchanged logic, new meaning of grace_until
    return self.is_owing and self.grace_until < today

def days_until_archive(self, today=None):   # new — drives the countdown in the banner
    return (self.grace_until - today).days
```

`is_in_grace` previously required `period_end < today`; it now requires `period_start <= today`.
An owing due before its period starts is neither in grace nor overdue — it is simply *issued*,
which is the gentlest of the three warning levels in §6.

---

## 5. Price changes

Already correct, and worth stating precisely because the interaction with lead time is not obvious.

`open_period` snapshots `plan.price_on(period_start)` — the price in force on the day the period
**starts**, not the day the invoice is raised. Add a `PlanPrice` with `active_from = 1 Jan 2027` and
every period starting on or after that date bills at the new amount. That is exactly "adjust the
price, effective as of the club's next billing cycle".

**The edge case that will bite:** periods are issued `renewal_lead_days` early. Enter a price change
on 15 December for periods starting 1 January, and any annual period already issued on 2 December
keeps the old amount — its `Due.amount` was frozen two weeks before the new price existed. The
snapshot is behaving correctly; the *operational* rule is what matters:

> Enter a price change before the renewal lead window opens for the periods it should apply to.

Two mitigations, both recommended:

1. Document the rule where prices are added (control-panel help text).
2. Warn in the control panel when a new `PlanPrice.active_from` falls inside a period that has
   already been issued, naming the affected clubs. Correcting one is then a deliberate
   cancel-and-reopen, not a silent surprise.

---

## 6. The club-facing warning

A new service — `billing/services/notices.py` — returns one small object for a club, or `None`:

```python
@dataclass(frozen=True)
class BillingNotice:
    level: str                  # "info" | "warning" | "error"
    due: Due
    amount_outstanding: Decimal
    grace_until: date
    days_until_archive: int
```

| Condition | Level | Message |
|---|---|---|
| Owing, `today < period_start` | `info` | Invoice outstanding, due by *grace_until*. |
| Owing, in grace, > 7 days left | `warning` | Fees are due. Club will be archived in *N* days. |
| Owing, in grace, ≤ 7 days left | `error` | Final notice. Archived in *N* days. |
| Owing, overdue | `error` | Archiving is pending. |
| Nothing owing | — | No banner. |

Rendered in `management/home.html`, in the existing alert block, and gated on `is_club_admin` the
same way the current billing banner is — ordinary members have no business seeing platform billing.
The existing "your period ends soon / renews automatically" notice is kept, at lower priority: it
answers a different question and only shows when nothing is owed.

All strings go through `{% trans %}` / `{% blocktrans %}` with `%(name)s`-style placeholders per
`CLAUDE.md` — the countdown is a `{% blocktrans count %}` so the plural form survives translation.

---

## 7. Services and commands

`billing/services/dues.py`:

| Function | Change |
|---|---|
| `subscribe(club, plan, …)` | Rename only. |
| `start_trial(club, trial_plan, *, post_trial_plan, start=None, …)` | **Drop `trial_months`** — the trial's length is now `trial_plan.duration_months`, and `period_end` no longer needs to be passed explicitly. |
| `open_period(…)` | Duration and grace come from the plan. Trial-conversion check unchanged. |
| `next_period_start` | Unchanged. |
| `dues_in_grace(today)` | `owing_dues().filter(period_start__lte=today, grace_until__gte=today)`. |
| `dues_overdue(today)` | Unchanged. |
| `archivable_clubs(today)` | Unchanged. |
| `subscriptions_due_for_renewal(today, lead_days=None)` | Per-plan lead. See below. |

`subscriptions_due_for_renewal` currently compares every subscription against one horizon in SQL.
With a per-plan lead the comparison is per-row. The function **already** materialises its result as
a Python list comprehension, so the honest move is to keep doing that and compare against each
plan's own lead:

```python
latest_period_end is None or latest_period_end <= today + timedelta(days=subscription.plan.renewal_lead_days)
```

At platform scale (tens to low hundreds of clubs) this is one query plus a list walk; pushing
portable date arithmetic into SQL to save that is not worth the opacity. The command's
`--lead-days` flag stays, redefined as a **global override** of the per-plan value, which is what
makes backfills and rehearsals possible.

`archive_overdue_clubs` needs no changes — but note that with the clock moving from ~410 days to
~60, this job goes from theoretical to load-bearing. Its dry-run-by-default posture matters more
now, not less, and `DEPLOYMENT.md`'s advice to run it without `--commit` for the first week should
be re-followed after this ships.

---

## 8. Migration plan

Four steps, in order. **Step 3 is the one that can take down live clubs.**

**1 — Rename.** `RenameModel` + `RenameField` operations. `makemigrations` will prompt for each
("Did you rename …?"); answer yes. **Inspect the generated migration by hand before running it** —
if Django emits `DeleteModel` + `CreateModel` instead of `RenameModel`, it will drop every price,
subscription and due in the table. This is the single highest-risk step in the change.

**2 — Add the new `Plan` fields**, with defaults chosen to reproduce *today's* behaviour on
existing rows: `duration_months=12`, `renewal_lead_days=30`, `is_trial=False`. `grace_days`
defaults to **30, not 45** — the number is not being carried over, because the thing it measures
from has changed.

**3 — Do NOT backfill `grace_until` on existing dues.**

> An open annual period that started in, say, March would today have `grace_until` around the
> following February. Re-derived under the new rule it becomes *March + 30 days* — a date already
> in the past. Every such club becomes instantly overdue, and the next `archive_overdue_clubs
> --commit` run switches off the entire paying customer base overnight.

Existing `Due.grace_until` values are snapshots and must be left exactly as they are. Only periods
opened *after* this ships use the new rule; every club migrates onto the new terms naturally at its
next renewal. If a specific club should move sooner, that is a deliberate one-off (cancel the open
period and reopen it), not a bulk data migration.

**4 — Retire the constants.** `GRACE_DAYS` and `RENEWAL_LEAD_DAYS` become the `default=` values on
the new `Plan` fields and are deleted from `billing/models.py`.

---

## 9. Blast radius

Everything below references `Tier`, the constants, or the grace semantics, and will need touching:

| Area | Files |
|---|---|
| Models & services | `billing/models.py`, `billing/services/dues.py`, **new** `billing/services/notices.py` |
| Commands | `billing/management/commands/renew_subscriptions.py` |
| Admin | `billing/admin.py` |
| Control panel | `controlpanel/views.py` (20 refs), `forms.py` (10), `urls.py` (3), `services/statistics.py` |
| Control-panel templates | `billing.html`, `_club_billing_card.html`, `_club_health_table.html`, `dashboard.html` |
| Club-facing | `management/views.py`, `management/templates/management/home.html` |
| Invoice | `billing/templates/billing/invoice.html` |
| Tests | `billing/tests.py` (16), `controlpanel/tests.py` (17), `management/tests.py` (3), `formbuilder/tests.py` (1) |
| Docs | `ARCHITECTURE.md` (new billing section), `DEPLOYMENT.md` (archive job note) |

`controlpanel/services/statistics.py` deserves particular attention: `tier_name`,
`dues_grace_until` and `dues_period_end` all feed the club-health table, and `dues_in_grace()` /
`dues_overdue()` feed the dashboard counters. Their *numbers* will move once the clock changes,
which is expected — but the annotations themselves need renaming, not just re-pointing.

---

## 10. Decisions taken, and the ones still open

**Taken:**

- Grace runs from `period_start`. *(Alternatives considered: from invoice issue — strictest, archives
  a club before it has used anything; from `period_end` — today's behaviour, ~410 days of unpaid
  use.)*
- Lead time is a per-plan field. *(Alternatives: derived from duration — invisible in the admin;
  one small global constant — too tight for an annual invoice paid by bank transfer.)*
- `Tier` → `Plan`.
- Trial length comes from the trial plan's own `duration_months`, not a per-subscription number.
- `post_trial_plan` stays on `Subscription`, not on `Plan` — the same trial can convert to different
  paid plans for different clubs.

**Resolved during implementation:**

1. **Full payment does NOT auto-restore an archived club.** `reactivate()` stays an explicit
   platform-admin action — a club can also be archived by hand for reasons that have nothing to do
   with money, and an automatic restore would silently reverse that the next time a stray payment
   was recorded. Instead, `_club_billing_card.html` shows a prominent prompt on any archived club
   whose dues are settled, so the deliberate act is one click away. No `archived_reason` field was
   needed.
2. **The banner escalates.** `management/home.html` renders it at every level; `management/base.html`
   repeats it on every *other* management page only once it reaches `error` (≤7 days, or overdue).
   Shown from the moment anything is owed it would sit on every screen for weeks and train people to
   ignore the one week that matters.
3. **Email reminders are in.** `send_billing_reminders` (dry-run by default, `--commit` to send)
   plus provider-agnostic SMTP settings read from the environment. Reminders go **once per
   escalation level**, tracked on `Due.last_reminder_level`, because the command is on a daily cron
   and a club that owes money for a month must not get thirty identical emails.
4. **Online payment stays out of scope.** Every payment is still recorded by hand by a platform
   admin (`record_payment`). The consequence is real and worth stating: the banner and the reminder
   email both tell a club admin money is due while giving them no way to pay it in-app. They pay by
   transfer; you record it.

### The email default that will catch you out

`EMAIL_BACKEND` defaults to the **console backend**, not SMTP. That is deliberate — Django's own
default tries localhost:25 and raises `ConnectionRefused` on a box with no MTA — but it means a
deployment that forgets `DJANGO_EMAIL_HOST` will watch `send_billing_reminders --commit` report
success while no club hears anything. Set the mail variables in `.env.production` (see
`.env.production.example`) before trusting the job.

## 11. Addendum: deleting a plan

Added after the initial implementation. `Due.plan` is `PROTECT` — a plan that has ever billed
anyone can never truly be removed, on purpose: `amount`, `period_end` and `grace_until` are frozen
on a `Due` precisely so a later change can't rewrite what was actually charged, and losing the plan
link off an old `Due` would do exactly that to every historical invoice. "Delete" therefore means
one of two things, chosen automatically (`billing/services/plans.py`):

- **No `Due` ever referenced the plan** (created, never actually used to bill anyone) — the row is
  removed outright.
- **At least one `Due` references it** — soft-deleted instead: `Plan.deleted_at` is set and
  `is_active` turned off. The row survives (so old invoices still say what they were billed under)
  but is hidden from every picker and listing via `Plan.objects.visible()` — an opt-in queryset
  method, same shape as `Club.objects.active()`, so the plain default manager stays unfiltered for
  Django admin and anything reading historical data.

Either way, every club **currently on the plan** is unsubscribed outright — its `Subscription` row
is deleted, not just its `plan` field cleared. "No plan" was already a state the rest of the app
fully understood (every billing view already handles `getattr(club, "subscription", None)` being
`None`), so this reuses it rather than inventing a new one.

One easy-to-miss second group: a club on a **different** plan, mid-trial, configured to convert to
the plan being deleted (`Subscription.post_trial_plan`). Left alone, that club's trial would try to
convert onto a plan that no longer exists (or has been hidden) the moment `open_period()`'s
trial-conversion check next runs. Handled at delete time instead: that club's trial is ended
(`trial_ends_at` and `post_trial_plan` both cleared, per the `CheckConstraint` that requires them
set together or not at all), leaving it on the trial plan with no scheduled conversion until a
platform admin picks a new one.

The confirmation screen (`controlpanel/templates/controlpanel/plan_delete.html`) is a real page,
not a modal like every other billing action — the whole point is naming exactly which clubs are
affected, in both groups, and that list can be long.
