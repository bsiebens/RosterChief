# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ClubManager is a **single-club** sport club management app + public website, built on **Django 6.0** (Python 3.14+). It is deliberately *not* multi-tenant — there is no `club_id` tenancy; the app manages one club.

The repo is currently an early **skeleton**: a stock `django-admin startproject` layout with only Django's built-in apps installed. None of the domain apps exist yet — see "Planned architecture" below for the intended shape (encoded in `pyproject.toml`, not yet on disk). Verify against the actual tree before assuming a module exists.

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

Settings live in a single `clubmanager/settings.py` and read from the environment via **python-decouple** (`config(...)`), with a local `.env` file for dev. Key vars: `DJANGO_SECRET_KEY` (required), `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DJANGO_DATABASE_URL`, `DJANGO_TIME_ZONE`.

The database is configured through a single `DJANGO_DATABASE_URL` (parsed by **dj-database-url**), defaulting to `sqlite:///db.sqlite3` for dev; production is intended to point at PostgreSQL via that URL. Don't hardcode DB settings — go through the env var.

## Planned architecture

`pyproject.toml`'s isort `known-first-party` list is the intended app decomposition — treat it as the roadmap when adding domain code:
`accounts`, `club`, `members`, `teams`, `events`, `news`, `pages`, `home`, `search`.

Domain notes (drive modeling decisions):
- **Season** is the central organizing concept. Team rosters, events, and attendance are season-scoped — model them with a FK to a season, not as global state.
- A **Member** can play on one or more **Teams**, each with a position + jersey number, always tied to a specific season.
- Three access tiers, implemented via Django groups/permissions: public site / members + parents / coaches + team managers.

## Conventions

- Ruff config anticipates a Wagtail-style codebase (`DJ` Django rules; `RUF012`/`RUF005` ignored for framework idioms; `line-length = 250`). Migrations are excluded from linting — don't hand-edit them to satisfy ruff.
- Settings files are exempt from `F403/F405/E501` (star imports allowed) under `clubmanager/settings/*` — note the config expects a settings *package*, though the current code is a single `settings.py`. If you split settings, match that path.
