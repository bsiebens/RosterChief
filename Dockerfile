# syntax=docker/dockerfile:1

# --- 1. the stylesheet -------------------------------------------------------
# Tailwind is a build-time concern: the CSS it emits is committed, but building it here means
# the image never depends on someone having remembered to run `npm run build`.
FROM node:22-slim AS css

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
# Every directory assets/app.css's @source lines scan -- miss one here and Tailwind's build
# silently emits no utilities for classes used only in that app's templates. Locally `npm run
# build` runs against the full checkout and never shows this; only a container image, built
# from just what's COPYed here, can.
COPY assets ./assets
COPY templates ./templates
COPY controlpanel ./controlpanel
COPY billing ./billing
COPY management ./management
COPY club ./club
RUN npm run build


# --- 2. the virtualenv -------------------------------------------------------
# Separate from the runtime for one reason: django-lucide is a *git* dependency (our lucide
# fork), so uv shells out to git to fetch it. python:*-slim has no git, and installing it in
# the runtime image would leave a build-time tool — plus its dependency tree — in production
# for the sake of one package that is already vendored into the venv by then.
FROM python:3.14-slim AS venv

RUN apt-get update && apt-get install --no-install-recommends -y git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Bytecode precompilation back on (see git history for the saga): phonenumbers'
# largest generated geodata/data*.py files (~900KB of literal dict data each)
# compile in ~0.1s on healthy hardware, but took 300s+ *per file* on the old
# build target -- a resource-starved VPS, not a slow file. The image now
# builds on GitHub Actions runners (native x86_64, unconstrained RAM) instead
# of that box, so there's nothing left to time out on; leaving this on means
# the compile cost is paid once here rather than on every container's first
# import (see .github/workflows/build-and-push.yml, DEPLOYMENT.md).
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first: they change far less often than the code, so this layer caches.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


# --- 3. the runtime ----------------------------------------------------------
FROM python:3.14-slim AS app

# WeasyPrint binds to these at import: no pango, no invoices. This is also why building the
# PDF path in a container is easier than on a Mac — apt has what Homebrew would have to.
RUN apt-get update && apt-get install --no-install-recommends -y \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libffi8 \
        libjpeg62-turbo \
        libopenjp2-7 \
        shared-mime-info \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    # gunicorn 26's control server puts a socket in $HOME. The app user has no home dir, so
    # without this it logs "Permission denied: /home/rosterchief" on every boot. /app is
    # already the workdir and owned by the app user, so point HOME there.
    HOME="/app"

WORKDIR /app

# The venv arrives fully built. Same base image, so the compiled wheels inside it are ABI
# compatible; nothing is re-resolved here, and no git is needed to run what git fetched.
COPY --from=venv /app/.venv ./.venv

COPY . .
COPY --from=css /build/static/css/app.css ./static/css/app.css
COPY --from=css /build/static/css/controlpanel.css ./static/css/controlpanel.css
COPY --from=css /build/static/css/management.css ./static/css/management.css

# collectstatic needs a settings module that imports: a throwaway key, never used at runtime.
RUN DJANGO_SECRET_KEY=build-only-not-a-secret \
    DJANGO_STATICFILES_BACKEND=whitenoise.storage.CompressedManifestStaticFilesStorage \
    python manage.py collectstatic --noinput

# mkdir before chown, and before the volume ever mounts: media_data has nothing to copy from
# at /app/media otherwise, so Docker creates the mount point itself, owned by root — and the
# app runs as rosterchief, not root. Existing image content (even an empty, correctly-owned
# dir) is what a named volume copies its initial ownership from on first use.
RUN useradd --system --uid 1000 rosterchief \
    && mkdir -p /app/media /app/private_media \
    && chown -R rosterchief /app
USER rosterchief

EXPOSE 8000

# Migrations are NOT run here. With more than one app container they would race, and a failed
# migration inside a starting web process is a bad place to find out — deploy runs them once,
# explicitly (see DEPLOYMENT.md).
# 2 workers, not 3: DEPLOYMENT.md's own sizing says this workload isn't CPU-bound, and each
# worker duplicates a full Django process — the single biggest lever on a memory-limited box.
# --preload imports the app once in the master and forks workers via copy-on-write instead of
# each re-importing Django independently (safe here: no app's ready() touches DB/Redis eagerly,
# checked club/features/news/events). --max-requests recycles a worker periodically so the one
# that happens to render a WeasyPrint invoice doesn't carry that +50-100MB forever.
CMD ["gunicorn", "rosterchief.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--threads", "4", \
     "--preload", \
     "--max-requests", "500", \
     "--max-requests-jitter", "50", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
