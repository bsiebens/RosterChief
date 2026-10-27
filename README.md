# RosterChief

A multi-tenant platform for running a sports club: members and families, teams and seasons, events and attendance, news, registration and payments, officiating, and a public website per club. One deployment serves many clubs, each on its own
subdomain (`ajax-united.rosterchief.app`).

Built on **Django 6** and **Python 3.14**. Server-rendered with HTMX and Alpine, styled with Tailwind and daisyUI. A PWA and a native-app API sit on top.

## What's in it

Each club gets:

- **Members and families.** People, households, and the accounts that manage them. A login belongs to the platform; everything else belongs to a club.
- **Teams and seasons.** Rosters, positions and jersey numbers per season, with the season as the central organising concept.
- **Events.** Training, games, tournaments and social events, with invitations, RSVPs, line-ups, tasks, recurring series and calendar feeds. Games can pull in competition data and live scores, and record a timeline of goals and penalties.
- **Officiating.** Referee and match-official sign-ups, arranging, and fees.
- **Registration, shop and billing.** Season sign-up, a cart and orders, dues and payments, and PDF invoices. The platform bills its clubs separately (`docs/BILLING.md`).
- **Forms and evaluations.** Admin-defined forms and player evaluation checklists.
- **News, announcements and notifications.** Including web push and native push.
- **A public website.** A marketing site on the base domain and a public site for each club.

Three surfaces serve different people:

| Path        | For                                                                                                   |
|-------------|-------------------------------------------------------------------------------------------------------|
| `/control/` | Platform operators: clubs, feature flags, maintenance mode, jobs, platform billing. Base domain only. |
| `/manage/`  | A club's admins, coaches and treasurers.                                                              |
| `/app/`     | Members and parents: the installable PWA.                                                             |

The native-app API lives under `/api/app/v1/` and is documented in `docs/app-api/`. The public API for club websites is under `/api/v1/`.

## Multi-tenancy in one paragraph

Row-based, shared schema. `Club` is the tenant root and every club-owned model carries a `club` foreign key; only `User` is global. The tenant is resolved from the request's subdomain, and every query is scoped to it. Roles are per club and checked
through a service layer, not Django's global permissions. The details are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), which is the authoritative design document.

## Getting started

You need [uv](https://docs.astral.sh/uv/) and, for the stylesheets, Node.

```bash
uv sync                                  # Python dependencies, including the dev group
npm install                              # front-end build tooling
cp .env.example .env                     # then set DJANGO_SECRET_KEY
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Club subdomains work on `localhost` out of the box: a club with the slug `ajax-united` is at `http://ajax-united.localhost:8000/`. The platform itself is at `http://localhost:8000/`. To see a populated club, run
`uv run python manage.py seed_demo_data --admin-email you@example.com` (an account that already exists; it is kept as the club's admin and everything else in the club is replaced).

PDF invoices use WeasyPrint, which needs pango and cairo. On a Mac: `brew install pango`.

### Stylesheets

`static/css/*.css` is pre-built, not live. After adding a new class to a template, rebuild that surface:

```bash
npm run build:management     # or :controlpanel, :mobile, :mobile-desktop, :marketing, :app
npm run build                # all of them
npm run watch:management     # rebuild on change
```

### Configuration

Settings read the environment through python-decouple, with a local `.env` for development. The database is a single `DJANGO_DATABASE_URL` (SQLite by default, PostgreSQL in production). `.env.example` lists every variable with comments.

## Everyday commands

```bash
uv run python manage.py test                       # all tests
uv run python manage.py test appapi                # one app
uv run python manage.py test appapi.test_member.CalendarTests.test_game_status
uv run python manage.py test --fast                 # quick tier, about a third of the time (CI runs it on feature branches; releases run everything)

uv run ruff check .                                # lint
uv run ruff format .                               # format
```

If the parallel test runner dies with `cannot pickle 'traceback' object`, a test failed and its report couldn't be sent between processes. Re-run with `--parallel 1` to see the real failure.

The app API's OpenAPI snapshot is checked by a test. After changing an endpoint or schema, regenerate it:

```bash
uv run python manage.py export_app_openapi
```

Every user-facing string must be translatable. See the Conventions section of [`CLAUDE.md`](CLAUDE.md) for the rules.

## Project layout

| Area             | Apps                                                                                    |
|------------------|-----------------------------------------------------------------------------------------|
| Platform         | `authentication`, `club`, `features`, `controlpanel`, `rosterchief` (settings, tenancy) |
| People and teams | `members`, `teams`, `registration`                                                      |
| Club life        | `events`, `announcements`, `news`, `notifications`, `evaluations`, `formbuilder`        |
| Money            | `shop`, `billing`                                                                       |
| Surfaces         | `management`, `mobile`, `marketing`                                                     |
| APIs             | `appapi` (native apps), `api` (public)                                                  |

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): the domain model, tenancy and access control.
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md): servers, TLS, backups, maintenance mode, the branching and release model.
- [`docs/API.md`](docs/API.md) and [`docs/app-api/`](docs/app-api/): the native-app API.
- [`docs/BILLING.md`](docs/BILLING.md): how the platform bills clubs.
- [`docs/GAME_TIMELINE.md`](docs/GAME_TIMELINE.md): game timelines and live pushes.

## Branching and releases

Git flow. Day-to-day work goes to `development`; a release is a `release/X.Y.Z` branch merged into `main`, which tags the version and builds the image. Fixes to a shipped version go out as `hotfix/X.Y.Z`. `deploy/cut-release.sh` runs the sequence,
and `docs/DEPLOYMENT.md` has the full description.

## Deploying

Production runs from container images that GitHub Actions builds, behind Caddy with a wildcard certificate (which needs DNS-01, so a DNS API token). From your own machine:

```bash
deploy/deploy-prod.sh                       # the latest main
deploy/deploy-prod.sh --version v0.8.1      # an exact release, or a rollback
```

The deploy puts the platform in maintenance mode, backs up the database, migrates, restarts, checks health, and then asks whether to reopen. [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) covers the first deploy and what makes this setup unusual.

## Licence

RosterChief is released under the [GNU Affero General Public License v3.0](LICENSE). If you run a modified version as a network service, the AGPL requires you to offer its source to your users.
