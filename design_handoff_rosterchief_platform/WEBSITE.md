# Handoff: RosterChief marketing website

Public-facing site for RosterChief: what the product is, what it costs, and how to reach Bernard. Single page, anchor navigation, no login. Companion to the app-design handoff in the same bundle (`README.md`) — **the tokens, fonts and component recipes there apply verbatim**; this document only covers what the website adds.

## About the design file

`RosterChief Website.dc.html` is a **design reference in HTML** — a prototype of intended look and behaviour, not production code. It uses inline styles and a custom runtime (`support.js`); **do not port the runtime.**

Recreate it in the RosterChief codebase (`bsiebens/RosterChief` — Django) using **Tailwind CSS**, with the `tailwind.config` extension from the app handoff. Fidelity: **high** — final copy, colours, type and layout. The one placeholder is the hero photograph.

## Page structure

Sticky header · Hero · Product (four surfaces + "one install, two hats") · Features · Pricing (tiers + add-on + FAQ) · Contact · Footer.

Container: `max-w-[1200px] mx-auto px-6`. Section rhythm: `py-[88px]`, alternating `bg-white` / `bg-paper` with `border-b border-line`; hero, contact and footer are dark (`bg-ink`, footer `#08101C`).

**This page is fluid, not fixed-width.** Every grid is `repeat(auto-fit,minmax(Npx,1fr))` except two that are deliberately locked: the six light feature cards are **always 3×2** (`repeat(3,minmax(0,1fr))`) and the four dark roadmap cards are **always one row** (`repeat(4,minmax(0,1fr))`). Headlines use `clamp()` — hero `clamp(44px,6.4vw,84px)`, section `clamp(34px,4.4vw,54px)`. Preserve both.

### Header
64px, `sticky top-0 z-50 bg-ink border-b border-hairline`. Crest clip-path + `RosterChief` wordmark on the left; nav right-aligned: Product / Features / Pricing in Barlow Condensed 700 15px `tracking-[.08em]` uppercase `text-onDark` → white on hover, then a club-red **Book a demo** button (`rounded-lg px-4 py-[9px]`).

### Hero
`bg-ink` with a full-bleed photo at `opacity-35` behind a `linear-gradient(105deg,#0B1220 0%,rgba(11,18,32,.94) 46%,rgba(11,18,32,.6) 100%)` scrim. Two columns (`minmax(320px,1fr)`), `gap-14`, `pt-24 pb-[88px]`.

Left: `ice` eyebrow "Club management for real seasons" → H1 **"Run the club, not the spreadsheet"** (Barlow Condensed 800, uppercase, `leading-[.92]`, `text-balance`) → 19px lede in `#C3CBD8`, `max-w-[56ch]`, `text-pretty` → two 54px CTAs (club-red **Book a demo**, outlined **See pricing**) → a mono trust line: `Free demo, no member limit · Your colours, your crest · Data stays in the EU`.

Right: three glass stat tiles (`bg-white/[.07] border border-white/[.12] rounded-[14px]`) reading **4** surfaces / **1** app (in `ice`) / **0** group chats — then a `navy` card, "The problem it removes", holding the two-paragraph story (nineteen families answering across three channels; RosterChief asks once) split by a `hairline` rule. That card is the page's argument — keep the specificity.

### Product
Four surface cards in `auto-fit minmax(260px,1fr)`, each with a 5px top bar in its accent (`club` management / `ice` coach / `ink` member / `muted` "we run the rest"), a mono audience line, a 28px condensed uppercase title, and a 15px paragraph.

Below, a `bg-paper border border-line rounded-2xl p-8` two-column block: **"Your coach is also somebody's parent"** with role chips (Coach of U16 · Parent of two · Div 4 player · **One account** in club red) beside a **290px phone mock** — navy header, crest, role switcher with Member active, dark next-up card with In/Out, and a skeleton card. Reuse the app's real switcher and card styling; this mock is the page's only product visual.

### Features
Six white cards, 3×2 fixed: Members & households · Attendance that closes · Line-ups & selection · Dues without chasing · News & public site · Your colours, your crest. Each: 26px club-red stroke-1.9 SVG icon, 22px condensed uppercase title, 15px paragraph.

Then four `bg-ink` cards in one row: Season calendar · Sign-up intake · Roles & rights · **Coming next** (shop, in-app renewal, federation licence sync — "on the roadmap, not in the price").

> **No online payments.** The dues card says invoices go out by mail and the treasurer marks off payments as they land. Do not reintroduce payment-provider copy anywhere.

### Pricing

Heading **"One price, one season, no surprises"**. Lede: one price covers every surface and every role, nothing metered — *"All prices exclude 21% VAT."*

Three tiers, `repeat(3,minmax(0,1fr))`, `gap-5`, equal height (`items-stretch`, CTA pinned with `mt-auto`):

| Tier | Price | Qualifier | Notes |
|---|---|---|---|
| **Free demo** | Free | `no member limit · no card needed · nothing to invoice` | Your real club, your real season. Everything ticked, incl. invoicing and "we import your members to start". CTA **Try it free** (outlined). |
| **Club** | **€ 500** `/ season, excl. VAT` | `up to 250 members · billed once per season · excl. 21% VAT` | `bg-ink` card, white text, ticks in `ice`, `shadow-[0_20px_50px_rgba(11,18,32,.22)]`. CTA **Book a demo** (club red). **No "most clubs" badge** — it was removed deliberately. |
| **Large club** | **Custom** | `quoted on your roster size · excl. 21% VAT` | More than 250 members. Everything in Club with no ceiling, migration, onboarding, a named contact. CTA **Ask for a quote** (outlined). |

There is **no Federation tier** — it was dropped on purpose. Don't add one back.

**Add-on band** (below the tiers, above the FAQ): a single bordered `rounded-2xl` card split into two columns — white left, `bg-ink` right.
- Left: club-red "Optional add-on" eyebrow with a hairline rule, **"We build your public website"**, a paragraph explaining that Club already includes a self-filling public site and this is the done-for-you option, then four ticks (Home/teams/calendar/news/join pages · crest, colours, photography · domain, mail, redirects · two rounds of changes).
- Right: **€ 750** with `one-off, from, excl. VAT`, a mono two-line note (`quoted after a look at what you have` / `excl. 21% VAT · no monthly fee on top of your plan`), and a club-red **Ask about a site** CTA.

**FAQ strip:** four short Q&As on `bg-paper`, `auto-fit minmax(240px,1fr)` — What counts as a member? (roster members only; staff, board and parents free; a player in two teams counts once, "so the 250 line is further away than it looks") · Moving in mid-season? (pro-rated, we import) · Any extra fees? ("None beyond VAT" — bank transfer, RosterChief issues invoices and tracks what's open) · Where is our data? (EU, exportable, one click and a CSV).

**VAT rule for implementation:** every displayed price carries an explicit VAT qualifier in the mono line next to or under it. If prices ever become configurable, keep the qualifier a required field rather than an optional suffix — Belgian clubs are mostly non-deductible and need the gross figure to be unambiguous.

### Contact
`bg-ink`, two columns `auto-fit minmax(300px,1fr)`, `gap-11`.

Left: `ice` eyebrow → **"Tell us about your club"** → a lede framing the demo as a real conversation, "half an hour, no slides" → three contact rows, each a 38px `navy` icon tile with a mono label above the value: **EMAIL** `bernard@rosterchief.app` · **REPLY TIME** Within two working days · **BASED IN** Mechelen, Belgium.

Right: a white `rounded-2xl p-[26px]` form card, **Book a demo**:

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | text | yes | placeholder "Bernard Siebens" |
| `email` | email | yes | placeholder "you@club.be" |
| `club` | text | yes | placeholder "Sharks Mechelen" |
| `members` | text | no | mono input, placeholder "312" |
| `role` | select | yes | Board / secretary · Treasurer · Coach / team manager · Federation · Something else |
| `msg` | textarea, 4 rows | no | placeholder "We chase attendance in six WhatsApp groups and the licence list lives in one person's inbox." |

Inputs: `min-h-[46px] border border-edge rounded-lg px-3 text-[15px]`, labels in Barlow Condensed 700 11px `tracking-[.12em]` uppercase `text-muted`, focus `outline-2 outline-ink outline-offset-1`. Submit: 52px club-red **Send to Bernard**. Under it, in 13px `text-muted`: goes straight to bernard@rosterchief.app, "No newsletter, no third parties, no follow-up sequence."

Success state replaces the form: green check tile, **"Your mail is ready"**, and a fallback mailto link.

## The contact form must be built server-side

**The prototype composes a `mailto:` link** — it prefills the visitor's mail client with a formatted body and shows the success card. That was right for a design file and is **wrong for production**: it fails on devices with no mail client configured, loses the lead silently, and can't be rate-limited.

Build it as a normal Django POST:

1. `ContactRequest` model — name, email, club, members (nullable int), role (choices), message, plus `created_at`, `ip`, `user_agent`. Persisting the lead matters more than the email; the mail is a notification, not the record.
2. `ContactForm` (`django.forms`) with server-side validation mirroring the required fields above. Return field errors inline under each input in `clubDark` — do not rely on browser validation alone.
3. On valid POST: save, then send mail to **bernard@rosterchief.app** with `reply_to=[form.cleaned_data['email']]` and a subject of `RosterChief demo request — {club}`. Body: club, members, role, name, email, then the message — same order as the prototype's mailto body, which is already a good triage format. Send through a real transactional provider (Postmark, Resend, SES) via `EMAIL_BACKEND`; queue it if a worker exists so a provider outage can't 500 the form.
4. Then redirect (POST-redirect-GET) to `#contact` with the success state rendered from a flash message — reproduce the "Your mail is ready" card, but reworded: the mail has actually been sent, so say so ("Thanks — that's with Bernard", same visual treatment).
5. Anti-spam: a honeypot field plus a timestamp check, or Django-ratelimit per IP. **No CAPTCHA** — the visitor is a volunteer club secretary, not an adversary.
6. Also accept `?plan=club|large|website` on the CTAs so the form can preselect context and the notification names which button they came from.

## Assets

- Fonts and colours: exactly as the app handoff (`Barlow`, `Barlow Condensed`, `IBM Plex Mono`; `ink`/`club`/`ice`/`paper`/`line` etc.).
- Icons: inline 24×24 stroke-1.9 SVGs, `currentColor`-ready.
- Crest: clip-path polygon `polygon(50% 0,100% 18%,100% 62%,50% 100%,0 62%,0 18%)`, same as the product.
- **Hero photography is not included.** The `<image-slot id="rc-web-hero">` marks one required photo: full-bleed, bench-level, faces and movement, dark enough to hold white type under the 105° scrim. Everything else on the page is type and colour.

## SEO & meta (not in the prototype — build it)

Title `RosterChief — club management for real seasons`; description from the hero lede; OG image from the hero photo with the wordmark; `Organization` + `SoftwareApplication` JSON-LD with `offers` reflecting the three tiers and `priceCurrency: EUR`; `lang="en"`. If Dutch is added later, the copy is written to translate cleanly — keep the condensed uppercase headlines short enough that Dutch compounds still fit on the same number of lines.
