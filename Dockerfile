# syntax=docker/dockerfile:1

# Every base image is pinned by version tag AND multi-arch index digest (the digest of the
# manifest list, not of one platform's image -- so the same line builds on amd64 and arm64).
# The tag is for humans; the digest is what's actually pulled, so a re-pushed tag can't swap
# the base under a rebuild. To bump: `docker buildx imagetools inspect <image>:<new-tag>` and
# copy the top-level "Digest:" line.

# --- 1. the stylesheet -------------------------------------------------------
# Tailwind is a build-time concern: the CSS it emits is committed, but building it here means
# the image never depends on someone having remembered to run `npm run build`.
FROM node:26.10.0-slim@sha256:ec7758ee051e457b468b32bde57b0879010b325bb9862718e9615225ce4aaae1 AS css

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
# Every directory any assets/*.css's @source lines scan -- miss one here and Tailwind's build
# silently emits no utilities for classes used only in that app's templates. Locally `npm run
# build` runs against the full checkout and never shows this; only a container image, built
# from just what's COPYed here, can. `registration` (assets/management.css's own @source) and
# `mobile` (assets/mobile.css's own @source) were missing here until this comment was added --
# management.css and mobile.css were each silently missing every utility class used only in
# that app's own templates, in every image built before this fix.
COPY assets ./assets
COPY templates ./templates
COPY controlpanel ./controlpanel
COPY billing ./billing
COPY management ./management
COPY club ./club
COPY registration ./registration
COPY mobile ./mobile
# assets/marketing.css's own @source -- the public marketing site.
COPY marketing ./marketing
RUN npm run build


# --- 2. the virtualenv -------------------------------------------------------
# Separate from the runtime for one reason: django-lucide is a *git* dependency (our lucide
# fork), so uv shells out to git to fetch it. python:*-slim has no git, and installing it in
# the runtime image would leave a build-time tool — plus its dependency tree — in production
# for the sake of one package that is already vendored into the venv by then.
FROM python:3.14.7-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS venv

RUN apt-get update && apt-get install --no-install-recommends -y git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /usr/local/bin/uv

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
FROM python:3.14.7-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS app

# WeasyPrint binds to these at import: no pango, no invoices. This is also why building the
# PDF path in a container is easier than on a Mac — apt has what Homebrew would have to.
# gettext supplies msgfmt, which `manage.py compilemessages` below shells out to -- Django
# has no pure-Python fallback for it.
RUN apt-get update && apt-get install --no-install-recommends -y \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libffi8 \
        libjpeg62-turbo \
        libopenjp2-7 \
        shared-mime-info \
        curl \
        gettext \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    # /app is root-owned and read-only to the app user (see the useradd step below), and
    # compose runs the whole root filesystem read-only with a tmpfs on /tmp. Everything that
    # wants a per-user writable spot therefore goes under /tmp: gunicorn 26's control socket
    # ($HOME/.gunicorn/), fontconfig's cache for WeasyPrint ($XDG_CACHE_HOME/fontconfig), and
    # anything else that reaches for ~. Worker heartbeat files and large-upload spill files
    # already default to /tmp via tempfile.
    HOME="/tmp" \
    XDG_CACHE_HOME="/tmp/.cache"

WORKDIR /app

# The venv arrives fully built. Same base image, so the compiled wheels inside it are ABI
# compatible; nothing is re-resolved here, and no git is needed to run what git fetched.
COPY --from=venv /app/.venv ./.venv

COPY . .
COPY --from=css /build/static/css/app.css ./static/css/app.css
COPY --from=css /build/static/css/controlpanel.css ./static/css/controlpanel.css
COPY --from=css /build/static/css/management.css ./static/css/management.css
COPY --from=css /build/static/css/mobile.css ./static/css/mobile.css
COPY --from=css /build/static/css/marketing.css ./static/css/marketing.css

# collectstatic needs a settings module that imports: a throwaway key, never used at runtime.
RUN DJANGO_SECRET_KEY=build-only-not-a-secret \
    DJANGO_STATICFILES_BACKEND=whitenoise.storage.CompressedManifestStaticFilesStorage \
    python manage.py collectstatic --noinput

# .mo files are gitignored (they're a compiled artifact of the committed .po sources, same
# idea as static/css/*.css above) -- built here so the marketing site's Dutch translations
# (marketing/locale/nl/) actually load. Without this, LocaleMiddleware still switches
# LANGUAGE_CODE correctly, but every {% trans %}/_() lookup finds no catalog and silently
# falls back to the English msgid.
RUN DJANGO_SECRET_KEY=build-only-not-a-secret python manage.py compilemessages

# Byte-compile the project's own code now, while the tree is still writable: the runtime user
# can't write __pycache__ into a root-owned /app (and PYTHONDONTWRITEBYTECODE says not to try),
# so without this every container start would recompile every module in memory. The venv was
# already compiled in its own stage (UV_COMPILE_BYTECODE).
RUN python -m compileall -q -j 0 -x '/\.venv/' /app

# The code is root's; the app user can read it but not change it -- so a compromised worker
# can't rewrite a template, a .py file or a collected static asset to persist itself. The only
# directories it owns are the two upload volumes' mount points. mkdir before chown, and before
# the volume ever mounts: media_data has nothing to copy from at /app/media otherwise, so
# Docker creates the mount point itself, owned by root. Existing image content (even an empty,
# correctly-owned dir) is what a named volume copies its initial ownership from on first use.
# Runtime scratch space is /tmp (see HOME/XDG_CACHE_HOME above), a tmpfs under compose.
RUN useradd --system --uid 1000 --no-create-home --home-dir /tmp rosterchief \
    && mkdir -p /app/media /app/private_media \
    && chown rosterchief /app/media /app/private_media
USER rosterchief

EXPOSE 8000

# Migrations are NOT run here. With more than one app container they would race, and a failed
# migration inside a starting web process is a bad place to find out — deploy runs them once,
# explicitly (see DEPLOYMENT.md).
# 2 workers, not 3: DEPLOYMENT.md's own sizing says this workload isn't CPU-bound, and each
# worker duplicates a full Django process — the single biggest lever on a memory-limited box.
# --preload imports the app once in the master and forks workers via copy-on-write instead of
# each re-importing Django independently (safe here: no app's ready() touches DB/Redis eagerly,
# checked club/features/news/events) -- and keeps the master free of threads and
# connections (the scheduler starts in the workers, gunicorn.conf.py's post_worker_init), so a
# fork can never copy a held lock into a worker. --max-requests recycles
# a worker periodically so the one that happens to render a WeasyPrint invoice doesn't carry
# that +50-100MB forever.
CMD ["gunicorn", "rosterchief.wsgi:application", \
     "--config", "gunicorn.conf.py", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--threads", "4", \
     "--preload", \
     "--max-requests", "500", \
     "--max-requests-jitter", "50", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
