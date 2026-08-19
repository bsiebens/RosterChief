# Handoff: RosterChief Platform — four surfaces, one system

## Overview

RosterChief is a sports club management platform (built for ice hockey clubs; Sharks Mechelen is the reference tenant). This handoff covers a complete visual and structural redesign of all four surfaces:

| # | Surface | Audience | Device |
|---|---------|----------|--------|
| 01 | **Control panel** | RosterChief staff (3–4 people) | Desktop only |
| 02 | **Club management** | Board, secretary, treasurer | Desktop only |
| 03 | **Coach mode** | Anyone with a staff role on a team | Mobile first |
| 04 | **Member mode** | Every member and every parent | Mobile first |

**The single most important structural decision:** surfaces 03 and 04 are *two modes of one installed app*, not two apps. A persistent Coach / Member switcher sits in the app header; it only appears for people who hold a staff role, and the chosen mode is remembered per device. Each mode has its own tab bar and its own navigation stack.

**The second:** there is no "parent app". A person is one account with a set of memberships and roles. Katrien Somers is simultaneously a Div 4 player, the mother of two U16 players, and head coach of U16 — three facts about one row, not three logins. Every per-member screen (attendance, profile, dues) carries a **person switcher** at the top listing the people that account manages, including "me".

## About the design files

The files in this bundle are **design references created in HTML** — prototypes showing intended look and behaviour, not production code to copy. `RosterChief Platform.dc.html` is a single-file design document containing all 25 screens laid out side by side on a canvas. It uses inline styles and a small custom runtime; **do not port that runtime.**

The task is to **recreate these designs in the RosterChief codebase** (`bsiebens/RosterChief` — Django, server-rendered templates) using its established patterns. Per the user's preference, **use Tailwind CSS** for styling: the token table below is written as a `tailwind.config` extension, and every measurement in this document maps onto a Tailwind utility.

The Django app boundaries the screens map onto are listed in `github.md` at the project root (`## Screen map`).

## Fidelity

**High-fidelity.** Final colours, typography, spacing and interaction affordances. Recreate pixel-accurately. Two deliberate exceptions:

1. **Photography is placeholder.** Every `<image-slot>` in the reference marks a spot where real club photography belongs (hero action shots, team photos, news covers, article portraits). Sizes and gradient scrims are final; the images are not.
2. **The iOS bezel is presentation only.** `ios-frame.jsx` draws a device frame so the mobile screens read as phone screens in the design document. The app content starts *below* a 54px status-bar inset and ends above a 26–30px home-indicator inset — preserve those safe areas via `env(safe-area-inset-*)`, not fixed padding.

---

## Design tokens

### Tailwind config

```js
// tailwind.config.js
export default {
  theme: {
    extend: {
      colors: {
        ink:    '#0B1220', // darkest — app chrome, sidebars, dark cards
        navy:   '#101E36', // member-mode app header, management sidebar accents
        steel:  '#1B2B47', // inset controls on dark (switcher track, chips)
        hairline: '#1E2B42', // rules on dark surfaces
        paper:  '#F4F5F7', // app/page background
        line:   '#E3E6EB', // 1px borders on light
        rule:   '#EEF0F3', // table row dividers
        edge:   '#D6DAE1', // stronger light border (control panel, inputs)
        stroke: '#C9CFD8', // secondary-button border
        muted:  '#6C7787', // secondary text
        dim:    '#8B95A4', // tertiary text, inactive tab icons
        slate:  '#3A4658', // body copy on light
        onDark: '#93A0B4', // secondary text on dark
        onDarkDim: '#7C8AA0',
        onDarkFaint: '#5C6B85',
        club:   '#E4002B', // CLUB ACCENT — themeable, see "Club theming"
        clubDark: '#B00021', // club accent, text-on-light / hover
        ice:    '#14B8E8', // coach-mode accent
        iceInk: '#04212C', // text on ice
        ok:     '#14A05A',
        okBg:   '#E6F6EE', okBorder: '#BFE7D3', okText: '#0C7A43',
        warn:   '#F0A22E',
        warnBg: '#FFF5E4', warnBorder: '#F6E0B8', warnText: '#9A6410', warnDeep: '#7A4E08',
        dangerBg: '#FDECEC', dangerBorder: '#F5C9CE',
        infoBg: '#EAF7FC', infoBorder: '#C3E7F4', infoText: '#0A6F91',
        rowSel: '#FFF7F8', // selected table row (club tint)
        rowFocus: '#F4F9FF', // focused/active row (info tint)
        rowWarn: '#FFFDF6', // row needing attention
        subhead: '#F8F9FA', // table header / section header fill
        violet: '#7C5CFC',  // calendar resource: training rink
      },
      fontFamily: {
        display: ['"Barlow Condensed"', 'sans-serif'],
        sans:    ['Barlow', 'system-ui', 'sans-serif'],
        mono:    ['"IBM Plex Mono"', 'monospace'],
      },
    },
  },
}
```

Google Fonts: `Barlow:400,500,600,700` · `Barlow+Condensed:600,700,800` · `IBM+Plex+Mono:400,500,600`.

### Type roles

| Role | Spec | Tailwind |
|---|---|---|
| Screen title (mobile) | Barlow Condensed 800, 22–26px, uppercase | `font-display font-extrabold text-2xl uppercase` |
| Screen title (desktop) | Barlow Condensed 800, 24px, uppercase | `font-display font-extrabold text-2xl uppercase` |
| Section headline (doc) | Barlow Condensed 800, 56px, `leading-[.95]`, uppercase | `font-display font-extrabold text-[56px] leading-[.95] uppercase` |
| Hero headline (mobile) | Barlow Condensed 800, 28–40px, `leading-[.96]`, uppercase | |
| Card title | Barlow Condensed 800, 20–24px, uppercase | |
| Eyebrow / label | Barlow Condensed 700–800, 11–12px, `tracking-[.14em]`, uppercase, `text-muted` | `font-display font-extrabold text-xs tracking-[.14em] uppercase text-muted` |
| Nav / button label | Barlow Condensed 800, 13–17px, `tracking-[.1em]`, uppercase | |
| Body | Barlow 400, 16px, `leading-[1.6]`, `text-slate` | |
| Lede | Barlow 600, 18px, `leading-[1.5]`, `text-ink` | |
| Row title | Barlow 600, 15px, `text-ink` | |
| Row meta | Barlow 400, 12–13px, `text-muted` | |
| **Scoreboard numeral** | Barlow Condensed 800, 26–76px, `leading-none`, `tabular-nums` | `font-display font-extrabold tabular-nums leading-none` |
| **Jersey number** | Barlow Condensed 800, 18–26px, `tabular-nums` | |
| Data / mono | IBM Plex Mono 400, 10–13px — IDs, money, timestamps, licence numbers, technical values | `font-mono` |

Rules: display type is **always uppercase**; body copy never is. Money always mono, always European format (`€ 780,00`). Dates in UI copy are `Sat 22 Aug`; dates in mono fields are ISO-ish (`2026-08-01`, `11.03.14-234.56`).

### Spacing, radius, borders

- Spacing: 4px base. Common: `gap-1.5 gap-2 gap-2.5 gap-3 gap-3.5 gap-4 gap-5`; mobile screen padding `px-4`; desktop content padding `px-7 py-6`; card padding `p-4` (mobile) / `p-[18px]` (desktop).
- Radius: mobile cards `rounded-[14px]`; nested/inner cards `rounded-xl`; desktop cards `rounded-xl`; buttons and inputs `rounded-lg`; chips/pills `rounded-full`; **control panel `rounded` (4px) — square by intent**; desktop frame `rounded-xl`.
- Borders: `border border-line` on light cards; `border-edge` in the control panel; `border-[1.5px] border-stroke` on secondary buttons; dividers `border-rule`.
- Shadows: only on the doc-level frames (`shadow-[0_24px_60px_rgba(11,18,32,.18)]`) and the member-detail drawer (`shadow-[-24px_0_60px_rgba(11,18,32,.2)]`). **Cards inside the product carry no shadow** — separation comes from hairlines.
- Minimum hit target: **44px** everywhere on mobile. Attendance in/out buttons are 44–46px tall; primary mobile CTAs 46–52px.

### Components

**Buttons** — height 46px mobile / 34–36px desktop, `rounded-lg`, label in Barlow Condensed 800 uppercase `tracking-[.1em]`:
- Primary: `bg-club text-white`
- Dark: `bg-ink text-white`
- Coach primary: `bg-ice text-iceInk`
- Positive (attendance In): `bg-ok text-white`
- Secondary: `bg-white border-[1.5px] border-stroke text-ink`
- Ghost on dark: `bg-white/15 text-white`

**Status pills** — `rounded-full px-2.5 py-[5px]`, Barlow Condensed 700, 11–13px, `tracking-[.1em]`, uppercase:
| State | Classes |
|---|---|
| In / Paid / Ready / Clean / Live | `bg-okBg text-okText border border-okBorder` |
| Out / Overdue / Transfer | `bg-dangerBg text-clubDark border border-dangerBorder` |
| No reply / Due / Watch / Medical | `bg-warnBg text-warnText border border-warnBorder` |
| Selected / Beta / Scheduled | `bg-infoBg text-infoText border border-infoBorder` |
| Draft / Optional | `bg-rule text-[#4A5566] border border-[#DDE1E7]` |

**Role switcher** — full-width segmented pill inside the app header. Track `bg-steel rounded-full p-1 gap-1` (member mode) or `bg-ink/`+`bg-steel` (coach mode); each segment `flex-1 h-9 rounded-full`; active segment is `bg-white text-ink` in Member mode and `bg-ice text-iceInk` in Coach mode; inactive `text-onDark`.

**Toggle** — `w-12 h-7 rounded-full` (mobile) / `w-10 h-[22px]` (desktop); on `bg-ok`, off `bg-edge`, beta `bg-ice`; knob is a white circle inset 3px.

**Bottom tab bar** — `bg-white border-t border-line pt-2 pb-[26px]` (member) or `bg-ink pt-2 pb-[26px]` (coach); four equal items, 48px tall, 21px stroke-2 icon over a Barlow Condensed 700 12px `tracking-[.08em]` uppercase label. Active colour = `club` (member) / `ice` (coach); inactive `dim` / `#6E7C93`.
- Member tabs: Home · Calendar · News · Me
- Coach tabs: Today · Squad · Schedule · Create

**Table row (desktop)** — CSS grid, 40px header row (`bg-subhead border-b border-line`, mono or Barlow Condensed 12px `tracking-[.12em]` uppercase `text-muted` headings), 46–52px body rows divided by `border-b border-rule`. Selected rows tint `rowSel`, focused row `rowFocus`, attention row `rowWarn`. Bulk-action bar appears as a **48px `bg-ink` strip directly above the table** with the count in Barlow Condensed uppercase and actions in `text-ice`.

**Club crest mark** — the shield is a clip-path, not an image: `clip-path: polygon(50% 0, 100% 18%, 100% 62%, 50% 100%, 0 62%, 0 18%)` on a solid block. Sizes: 20×22 (preview), 28×30 (sidebar), 30×32 (app header), 64×68 (control panel club header). Replace with the club's real SVG logo where one is uploaded; the clip-path is the fallback.

---

## Club theming

Club customisation is **club-level only**: primary colour, secondary colour, logo, wordmark. Model fields already exist (`club/models.py`: `Club.primary_color`, `Club.secondary_color`, `Club.logo`).

- `secondary_color` drives the **club accent** (`--club`): app header active tab, primary buttons, section eyebrows, selected-row tint, public-site nav and join CTA, invoice/email headers.
- `primary_color` drives dark chrome where the club overrides the platform navy.
- **Never themeable:** status colours, type, spacing, neutrals, table chrome, form fields. This is what keeps every club legible and every screen familiar. Implement as CSS custom properties set on `:root` per tenant, consumed by Tailwind arbitrary values (`bg-[var(--club)]`) or a `club` colour mapped to `var(--club)`.
- Coach mode uses `ice` (#14B8E8) regardless of club — the mode signal must survive theming.
- The control panel is **never** club-branded.
- Escape hatch: a per-club custom stylesheet for the public site only (`custom_stylesheet` feature flag).

---

## Screens

### Member mode (mobile, 402×874 reference)

**M1 · Home.** Navy header: crest + club name + season, bell with unread dot, role switcher below. Body scrolls: person switcher chips (avatar + first name; active chip `border-[1.5px] border-ink`, plus a "Me" chip) → dark hero card with 120px photo, gradient scrim, `NEXT UP · SAT 22 AUG` eyebrow in `ice`, match title, meta row (face-off / meet / venue), then "Lars — are you in?" with In (`bg-ok`) / Out (`bg-steel`) 46px buttons → "Needs your answer" card with a count in club red and dated rows carrying `REPLY` pills → dues card (€ badge, amount, due date, Pay button) → news teaser card with 104px cover, club-red category eyebrow and condensed uppercase headline.

**M2 · Event · answer for several.** 250px full-bleed photo header with a two-stop scrim, circular back button at the safe-area top, `HOME GAME` club-red badge and a 38px condensed uppercase title. Body: detail card (Face-off / Meet / Where + address / Kit as label-value rows with 78px labels) → "Your answers" card with **one three-state segmented control per person** (In / Maybe / Out, 44px), each person shown with avatar, name, team · number · position, and a `NO REPLY` pill where unanswered; "Add a note for the coach" affordance below → squad-response card with a stacked in/out/silent bar and counts.

**M3 · Calendar.** Navy header with title, member-scope pill, and List / Month / Games-only filter chips. Body is a 1px-gapped list on a `line` background, grouped under sticky `THIS WEEK` / `NEXT WEEK` labels. Each row: day-of-week + big condensed date, a 3px colour bar for event type (ice = `ice`, other = `warn`) or a 4px left border in club red for games, title, meta (time · team · which of my people), and a status pill (In / Out / Reply / 1 open / Optional).

**M4 · News article.** 330px portrait photo, three-stop scrim, back button, `ice` eyebrow (team · date), 40px condensed uppercase headline stacked over three lines. Body on white: 18px semibold lede, 16px body paragraphs, tag pills, then a byline row with avatar and a Share secondary button.

**M5 · Me & my people.** Navy header with 56px avatar, name, "Member since · role". Body: "People I manage" card — one row per managed person plus the account holder marked `(me)`, each with team · number · licence state (problems in `clubDark`) and a chevron → settings list (Personal details, Household & contacts, Payments & dues with a `1 OPEN` pill, Notifications) → dark "Coach mode" promo card with an `ice` icon tile → version line.

**M6 · Edit personal info.** White sticky header: back chevron, subject's name, Save button in club red. Body: warning banner for the missing medical form → grouped label-value cards (Identity, Contact, Emergency, Consent). Values are 16px medium; mono for dates, register numbers and phone numbers. Consent rows carry 48×28 toggles.

**M7 · Notifications.** Navy header: "Inbox", "Mark all read" in `ice`, filter chips (All / Action `3` / Club). Body grouped by Today / Earlier this week, rows on a 1px-gapped list. Actionable rows carry a **4px left border** (`club` for action-now, `warn` for warnings) and their action inline: In/Out buttons, Upload, Pay. Informational rows are flat; read rows drop to `opacity-[.72]`. Footer line points at Me → Notifications for push preferences.

### Coach mode (mobile, dark chrome)

**C1 · Today.** `ink` header: `ice` crest, team name, "Head coach · name", a team-picker pill, then the role switcher with Coach active (`bg-ice`). The body is a light sheet that **overlaps the header with a 20px top radius** — this is the mode's signature. Content: three stat tiles (Squad / In Sat / Silent, silent in club red) → tonight's session card with an `ink` header strip (`TONIGHT · 19:15` in `ice`) and a 50px `bg-ice` "Check attendance" CTA + overflow button → "Needs you" list, each item a card with a 4px left border by severity (line-up = `club`, silent players = `warn`, member blocker = `ice`) and a right-aligned action → "Also yours": a single `navy` card surfacing the coach's *member-side* obligation, so the two hats never fight.

**C2 · Bench attendance.** `ink` header with back chevron, "Attendance", session meta, and a progress bar + `14/19` counter. Light sheet: filter chips (All 19 / Silent 5 / Goalies) then a 1px-gapped roster list. Each row: jersey number (condensed 22px tabular), name, position, and a **joined 92×44 two-button control** — check (left, `bg-ok` when in) and cross (right, `bg-club` when out); unset is `bg-rule` with `dim` glyphs. Silent players' rows tint `rowWarn`. Fixed white footer with a 52px `bg-ink` "Save attendance" button.

**C3 · Game selection / line-up.** Fully dark screen. Header: back, "Line-up", opponent + date, `bg-ice` Publish button, then a mono-ish meta row (dressed / goalies / scratched). Body: one `navy` card per unit (Line 1, Line 2, Defence pairs) containing a grid of player tiles — `bg-steel rounded-[10px]` with a 26px condensed jersey number over a surname; empty slots are `border-[1.5px] border-dashed border-[#2C3B56]` reading `EMPTY`. Below: "Available · drag into a slot" pills; unavailable players (out / silent) are shown at `opacity-50` with the reason appended.

**C4 · Create event.** White sticky header: Cancel / "New event" / Create (`bg-ice`). Body: three event-type tiles (Practice active in `ink`, Game, Other) → label-value card (Title, Date + Time side by side, Location) → "Who" pills (team with count active, other teams, Goalies only, Pick players) → options card (Ask for attendance toggle, Answers close row, Repeat weekly toggle with the resulting event count) → an `infoBg` note stating how many members get notified and how many have a clash.

**C5 · Post news.** White sticky header with a club-red Publish. Keyboard is up (this screen is shown mid-composition). 120px cover slot, then the composer: condensed uppercase 28px headline, a 40×2 club-red rule, 16px body with a **club-red caret** at the insertion point, tag pills, and an Audience row ("U16 families · also on club website").

**C6 · Add members to team.** `ink` header with back, "Add to U16", squad count, and a `bg-steel` search field. Light sheet: filter chips (Suggested / Age eligible / No team), then sections — "Moving up from U14", "New this season" — of rows with avatar, name, `year · position · licence state`, and a 28px square checkbox (`bg-ok` with a check when selected, `border-[1.5px] border-stroke` when not). Members with a licence problem show it in `clubDark`. Fixed white footer: "2 selected / Squad becomes 21" beside a 52px `bg-ice` Add button.

### Club management (desktop, 1440×900 reference)

Shared shell: **236px `bg-ink` sidebar** (crest + club name + `RosterChief · management` in mono, then 40px nav items in Barlow Condensed 700 16px `tracking-[.06em]` uppercase; active item `bg-club text-white rounded-lg`; expanded sub-items are 28–30px 14px rows indented 22px, active in white semibold, counts as pills) + **64px white topbar** (screen title, mono context, spacer, then secondary actions and one club-red primary) + content on `paper`. Several screens add a 48–54px filter/tab strip under the topbar. **Every management screen fits 900px without internal scrolling** — keep that constraint.

**D1 · Club home.** Five KPI cards (Members, Awaiting approval, Dues collected, Licences missing, Turnout) with 44px condensed numerals and a delta line. Then a 1.35fr/1fr split: left — "Needs attention" list where each row has a 6px severity bar, a title, a detail line and a right-aligned action (Review / Export / Chase / Assign), and below it a "Membership by team" bar chart (ten bars, the focused team in club red, the rest `navy`); right — a dark "This weekend" card (three fixture rows with big condensed dates) over a "Recent activity" card (mono timestamps + actor-first sentences).

**D2 · Members list.** Topbar with count and Import CSV + New member. Filter strip: search field, applied filters as removable `bg-ink` chips, `+ Filter`, saved-view dropdown. **Bulk bar** (`bg-ink`, 48px): "3 selected", divider, then Move to team / Assign role / Send message / Create invoice in `ice`, Clear on the right. Table columns: checkbox · No. (condensed 18px) · Member (30px avatar + name + `year · sex`) · Team · position · Licence (mono, `okText` or `clubDark` "missing") · Dues pill · Attendance (mono %) · Household. Selected rows tint `rowSel`. Pager row at the bottom.

**D7 · Member detail.** The members table dimmed to `opacity-50` under a `rgba(11,18,32,.34)` scrim, with a **620px right drawer**. Drawer header: 56px avatar, name + club-red `#9`, a mono provenance line (`member since · id · team · household`), Message + close. Tabs: Profile / Attendance / Finance / Documents / History. Profile body: warning banner naming both blockers with a Request action → two cards side by side (Identity as label-value rows with mono values; Household listing every related person with their relationship, payer and staff roles, plus a sibling-discount note) → season card with a 12-bar attendance sparkline (`ok` present, `club` absent, `edge` upcoming) beside Present / Absent / No-reply numerals → three small stat cards (Open balance, Plan, App). Footer: Save changes (club red), Move team (secondary), and **End membership as red text, never a button**.

**D3 · Sign-up intake & approval.** Two-pane: left, the queue table (Applicant with source line · Born · Wants · Checks pill · Age, oldest highlighted with a 3px `ice` left border and `rowFocus` tint); right, a **420px detail pane** — applicant header, Checks list (20px square icons, `ok` for passes, `warn` with `!` for gaps), "Place in" team buttons, a fee-plan mini-table ending in an instalment row on `subhead`, and the applicant's own note as a quote. Footer: "Approve & invoice" (`bg-ok`, flex-1) + Hold (secondary).

**D4 · Team & staff assignment.** 180px team cover photo with a left-weighted scrim, `ice` category eyebrow, 52px condensed team name, meta row, and Export roster / Add player buttons bottom-right. Tab strip: Roster / Staff / Schedule / Attendance / Results / Settings. Content 1.55fr/1fr: left, the roster table (No. · Player with C/A letters in club red · Position · Shoots · Status pill; attention rows tinted); right, a Staff card (avatar, name, `role · rights`, mono "since YYYY"), a "Squad make-up" card (Goalies / Defence / Forwards numerals + the federation minimum stated in prose), and a dark "Blocking the season start" card listing blockers with `ice` actions.

**D5 · Season calendar planning.** Sidebar gains an "Ice resources" legend (main rink `ice`, training rink `violet`, off-ice `warn`, games `club`). Topbar: week number, mono date range, prev/next, Week/Month/Season switch, "Plan recurring". Main: a 7-column week grid with a 54px mono hour gutter (17:00–23:00) and absolutely positioned event blocks coloured by resource; the focused team's block is `bg-ink` with a 2px club-red border and shows its attendance split. Right rail (300px): a "Plan recurring" form (Team / Pattern / Range / Skip) ending in "Generate 32 events", a Conflicts list (double booking in danger colours, players in two teams in warn colours), and a footnote that publishing pushes to member apps and the public site together.

**D6 · Dues & billing.** Topbar: Export SEPA / Send reminders / New invoice run. Row one: a dark "Collected" card (46px `€ 84.240`, a `68%` figure in `ice`, a three-segment progress bar, target and last-year comparison) plus three light cards (Open, Overdue 30+ in club red, Instalment plans). Row two 1.7fr/1fr: left, the invoice table (mono invoice number · Household · For · Amount · Due · Age, overdue ages in `clubDark`) with Overdue/All chips in its header; right, an "Aging" card (four labelled bars: not due `ink`, 1–30 `warn`, 31–60 `club`, 60+ `#8C0019`) over a "Reminder ladder" card (four numbered steps escalating in colour) with a note that suspension is a club setting and never automatic for youth.

**D8 · News list & editor.** Three panes. **400px list pane:** topbar with New post, filter chips (All 42 / Drafts 3 / Scheduled 1), then post rows — status pill + mono meta (date · reads, or "edited N ago"), condensed uppercase headline, `author · team · category`; the open draft is tinted `rowSel` with a 3px club-red left border. **Editor:** its own topbar (mono autosave state, Preview / Schedule / Publish), then a white article card with a 190px cover slot and the article rendered at final typography — club-red category eyebrow, 42px condensed uppercase headline, 56×3 club-red rule, semibold lede, body paragraphs, club-red caret. **300px right rail:** Audience toggles (team families / whole club / public website), a push-reach note, tags, and a note that coaches can post to their own team from the app while club-wide and website posts need a news role.

**D9 · Club identity & branding.** Topbar shows "unsaved changes" in `warnText` with Discard / Save. Two columns. Left: **Club** card (Club name, Short code in mono, Tagline, Website in mono, Federation), **Colours** card (primary + secondary swatch inputs with mono hex, an "accents only" note in its header, and a contrast-check row showing computed ratios with ✓), **Logo & wordmark** card (76px dashed drop zones for crest SVG and wordmark). Right: **Live preview** card with App / Website / Email tabs — a 232px phone mock (header, role switcher, hero card, skeleton rows) beside a website-header mock and a "Where the brand shows up" list that ends with an explicit *never* (status colours, tables, form fields); below it an **Advanced** card (custom stylesheet with file size and edited date, own domain with a verified state) marked "enabled by RosterChief".

### Control panel (desktop, 1440×900)

Deliberately industrial: 4px radii, hairline `edge` borders, mono figures, no decoration. Shared shell: **52px `bg-ink` command bar** (mark + `RosterChief` + mono `control`, then mono tabs — active tab `bg-steel` with a 2px `ice` bottom border — spacer, a `⌘K run command` field or a primary action, and a live status dot) over a **34px white breadcrumb/metrics strip** (mono: environment, deploy, p95, queue depth, alert count in `clubDark`).

**P1 · Platform health.** Six KPI tiles (clubs live, members, WAU, MRR, zero-event clubs in `warn`, failed jobs in `club`) with mono labels and 38px condensed numerals. Below, 1.6fr/1fr: left, a stacked 12-week sign-up chart (youth `ice` over adult `ink`, square bars, mono week ticks) over a "Club health" table (club · members · WAU · events 30d · plan · risk pill, sorted by risk, the reference tenant tinted `rowFocus`); right, a dark **Alerts** card (mono entries with 2px severity left borders), a **Feature adoption** card (mono flag names with `n/34` counts and square progress bars), and a **Job log** card (mono `time · ok|err · job · detail`).

**P2 · Club provisioning & feature flags.** Command bar carries a club-red "Provision club" action; breadcrumb strip shows `clubs / slug / settings` plus club id and creation date. Left rail (280px) is a mono club list with health dots and member counts, active club inverted to `bg-ink`. Content: club header (64px crest, 34px condensed name, mono domain/federation/counts line, then Impersonate / Audit log / Save). Below, two columns: left, a **Branding** card (primary + secondary swatch fields, logo and wordmark previews, and a note that colours apply to accents only) over a mono **Plan & billing** card (plan, seats, monthly, renews, last invoice in `ok`); right, a **Feature flags** table — one row per flag with the mono flag name, a plain-language description, an optional `beta` / `soon` chip, and a toggle. Flags shown: `public_site`, `online_payments`, `lineups`, `licence_sync_rbihf`, `instalment_plans`, `licence_suspension`, `shop_beta`, `season_registration`, `custom_stylesheet`, `multi_sport`.

---

## Interactions & behaviour

**Mode switching.** The switcher renders only if the account has ≥1 staff assignment. Switching swaps the tab bar, the chrome palette (navy/club ↔ ink/ice) and the navigation stack; each mode keeps its own stack position. Persist the last mode per device and restore on launch. Deep links from a notification open the correct mode regardless of the stored one.

**Person scope.** The person switcher is a horizontally scrolling chip row. Changing it re-scopes the current screen without navigating. Managed people come from the household/family relation; "Me" appears when the account holder is themselves a member. Calendar has an extra "All members" scope.

**Attendance.** Three states — in / maybe / out — plus an implicit *no reply*. Answers close at a per-event deadline (default 24h before start); after that the control becomes read-only with the reason shown. Coach-side attendance (C2) records actual presence, which is a separate axis from the member's RSVP; both feed the attendance percentage on the member record.

**Line-up.** Drag a player from the Available row into a unit slot; slots accept one player and swap on drop. Out and silent players stay visible but non-draggable at 50% opacity. Publish notifies only selected players and writes the line-up to the game record.

**Recurring events.** The planner previews the count before writing ("Generate 32 events"). Conflicts are computed against ice resources and against members in two teams, and are shown before generation, not after.

**Bulk actions.** Selecting rows reveals the dark bulk bar; the count is authoritative and actions apply to the selection, not the filter. Clear deselects without resetting filters.

**Approval.** "Approve & invoice" creates the membership, places the person in the chosen team, applies the fee plan, and issues the invoice in one action. Open checks do not block approval — they carry over as tasks on the member record.

**Motion.** Restrained. Sheet transitions 240ms `cubic-bezier(.2,.8,.2,1)`; drawer slide 240ms; pill/toggle state 120ms; counters may count up on first paint (≤600ms) but nothing loops. No parallax, no decorative animation.

**States to build that the mocks imply.** Empty (no events / no news / no managed people), loading skeletons matching card geometry (see the D9 preview's skeleton rows for the intended treatment), offline banner for the coach at the rink (attendance must queue and sync), form validation inline under the field in `clubDark`, and permission-denied where a coach lacks a right (hide, don't disable, except where the absence would be confusing).

## State

Mobile: `mode` (member|coach, persisted), `scopePerson`, `activeTeam` (coach), per-event `rsvp[personId]`, attendance draft `{memberId: in|out|unset}` (offline-queued), notification read state, filter selections.

Desktop: route, table filters + saved view, selection set, drawer target + tab, editor draft with autosave timestamp, dirty-form flag (D9 shows it explicitly in the topbar).

## Assets

- **Fonts:** Barlow, Barlow Condensed, IBM Plex Mono (Google Fonts, all OFL).
- **Icons:** inline 24×24 stroke-2 SVGs, `currentColor`-ready — bell, home, calendar, news, person, clock, person-plus, plus, chevron, check, cross, search, chart, building, dots. Swap for the codebase's existing icon set if one exists; keep 21px at 2px stroke on mobile tabs.
- **Crest:** clip-path polygon fallback plus `uploads/rosterchief-dark.svg` (the RosterChief shield). Club crests come from `Club.logo`.
- **Photography: not included.** Every `<image-slot>` marks a required real photo: M1 hero + news cover, M2 game action, M4 article portrait, C5 news cover, D4 team cover, D8 article cover.

## Files in this bundle

| File | What it is |
|---|---|
| `RosterChief Platform.dc.html` | The design document — all 25 screens. Open in a browser; it is the visual source of truth. |
| `ios-frame.jsx` | Presentation-only iOS bezel used by the mobile screens. Not for production. |
| `image-slot.js` | Photo placeholder component. Not for production. |
| `support.js` | Runtime for the design document. **Do not port.** |
| `rosterchief-dark.svg` | RosterChief shield mark. |
| `github.md` | Repo association and the screen → Django app map. |

## Suggested build order

1. Tokens: Tailwind config, fonts, per-tenant CSS custom properties for club colours.
2. Primitives: button, pill, toggle, chip, label-value row, table row, card, tab bar, app header + role switcher.
3. Member mode M1 → M3 → M2 → M7 (the RSVP loop is the product's core).
4. Coach mode C1 → C2 → C3 (attendance and line-up are the reason coaches install anything).
5. Management D2 → D7 → D3 (member data, then intake), then D4, D5, D6, D8, D9.
6. Control panel P1, P2.
