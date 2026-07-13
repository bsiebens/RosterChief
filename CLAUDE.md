# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

RosterChief is a sport club management app + public website, built on **Django 6.0** (Python 3.14+). As of **2026-07-11 it is designed as a multi-tenant platform** (row-based / shared-schema): one deployment serves many clubs, with `Club` as the tenant root. Every club-owned model carries a `club` FK (via `ClubScopedModel`); `User` is the only global model. This **reverses** the project's earlier single-club stance — treat older "single-club / no `club_id` tenancy" notes (in git history or memory) as obsolete.

**`ARCHITECTURE.md` at the repo root is the authoritative model & domain design** — the tenancy mechanics, the RBAC design, and per-app model sketches all live there. Consult and update it when adding domain models.

The repo is an early build: `authentication` and `club` apps exist (`User`, `Member`, `Family`, `FamilyMembership`, `Club`, `ClubMembership`); the remaining domain apps and the tenancy plumbing (`rosterchief/tenancy.py`, tenant middleware, `ClubScopedModel` upgrade) are **planned, not yet on disk**. Verify against the actual tree before assuming a module exists.

## Commands

Dependencies and the virtualenv are managed with **uv** (`pyproject.toml` at repo root, `uv.lock` committed). Run Django/tools through `uv run` so the project venv is used.

```bash
uv sync                              # install deps (incl. dev group) into .venv
uv run python manage.py runserver    # dev server
uv run python manage.py migrate      # apply migrations
uv run python manage.py makemigrations
uv run python manage.py createsuperuser
uv run python manage.py shell

uv run python manage.py test                     # run all tests (Django test runner)
uv run python manage.py test <app>               # one app
uv run python manage.py test <app>.tests.<Case>  # one TestCase
uv run python manage.py test <app>.tests.<Case>.<method>  # one test

uv run ruff check .                  # lint
uv run ruff check --fix .            # lint + autofix
uv run ruff format .                 # format
```

## Configuration

Settings live in a single `rosterchief/settings.py` and read from the environment via **python-decouple** (`config(...)`), with a local `.env` file for dev. Key vars: `DJANGO_SECRET_KEY` (required), `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DJANGO_DATABASE_URL`, `DJANGO_TIME_ZONE`.

The database is configured through a single `DJANGO_DATABASE_URL` (parsed by **dj-database-url**), defaulting to `sqlite:///db.sqlite3` for dev; production is intended to point at PostgreSQL via that URL. Don't hardcode DB settings — go through the env var.

## Planned architecture

**`ARCHITECTURE.md` is the source of truth for the model design; this is a summary.** The app decomposition (`authentication`, `members`, `club`, `teams`, `events`, `news`, `pages`, `home`, `formbuilder`, `shop`, `search`) has grown past the original `pyproject.toml` isort `known-first-party` list — add new labels there as apps land. Note the `accounts` app was split into `authentication` (global login) + `club`, and people models (`Member`, `Family`) are being moved into a dedicated `members` app.

Domain notes (drive modeling decisions):
- **Multi-tenancy is the cross-cutting rule.** `Club` is the tenant root; club-owned models inherit `ClubScopedModel` (a `club` FK). Scope every query to the current tenant (`.for_club()` / `.current()`); previously-global uniqueness (slugs, season names, invoice numbers) becomes **unique per club**. Only `User` is global. See `ARCHITECTURE.md` §2.4.
- **Season** is the central organizing concept, **per club**. Team rosters, events, and attendance are season-scoped — model them with a FK to a season, not as global state.
- A **Member** (a person *within one club*) can play on one or more **Teams**, each with a position + jersey number (unique within a team), always tied to a specific season.
- **RBAC is per-club and service-layer** (not `django-guardian`, not global Django groups): `ClubRole` rows (`MEMBER` / `EDITOR` / `TREASURER` / `BOARD`) plus object-scoped roles (coach via `StaffAssignment`, parent via `FamilyMembership`), all decisions routed through an access service. Django's own permissions are used only for the platform-admin layer.
- Later modules: `formbuilder` (admin-defined dynamic forms → normalized answers → reporting) and `shop` (cart → order → payment → HTML→PDF invoices via WeasyPrint), with season-scoped `ClubMembership` tracking sign-up + fee status per season.

## Conventions

- Ruff config anticipates a Wagtail-style codebase (`DJ` Django rules; `RUF012`/`RUF005` ignored for framework idioms; `line-length = 250`). Migrations are excluded from linting — don't hand-edit them to satisfy ruff.
- Settings files are exempt from `F403/F405/E501` (star imports allowed) under `rosterchief/settings/*` — note the config expects a settings *package*, though the current code is a single `settings.py`. If you split settings, match that path.
