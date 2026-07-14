# syntax=docker/dockerfile:1

# --- 1. the stylesheet -------------------------------------------------------
# Tailwind is a build-time concern: the CSS it emits is committed, but building it here means
# the image never depends on someone having remembered to run `npm run build`.
FROM node:22-slim AS css

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY assets ./assets
COPY templates ./templates
COPY controlpanel ./controlpanel
COPY billing ./billing
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
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# The venv arrives fully built. Same base image, so the compiled wheels inside it are ABI
# compatible; nothing is re-resolved here, and no git is needed to run what git fetched.
COPY --from=venv /app/.venv ./.venv

COPY . .
COPY --from=css /build/static/css/app.css ./static/css/app.css

# collectstatic needs a settings module that imports: a throwaway key, never used at runtime.
RUN DJANGO_SECRET_KEY=build-only-not-a-secret \
    DJANGO_STATICFILES_BACKEND=whitenoise.storage.CompressedManifestStaticFilesStorage \
    python manage.py collectstatic --noinput

RUN useradd --system --uid 1000 rosterchief && chown -R rosterchief /app
USER rosterchief

EXPOSE 8000

# Migrations are NOT run here. With more than one app container they would race, and a failed
# migration inside a starting web process is a bad place to find out — deploy runs them once,
# explicitly (see DEPLOYMENT.md).
CMD ["gunicorn", "rosterchief.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
