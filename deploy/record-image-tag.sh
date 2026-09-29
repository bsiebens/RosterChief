#!/usr/bin/env bash
# Record the image tag a deploy just restarted `web` on as IMAGE_TAG= in the compose .env, so
# a later by-hand `docker compose run ... web` (a one-off command, e.g. import_member_photos)
# uses the image that's actually deployed rather than the `:main` fallback in compose.yaml.
#
#   deploy/record-image-tag.sh v0.7.12        # run from the repo root, next to .env
#
# Called by deploy-local.sh and deploy-dev.sh right after they restart web; there is no need to
# run it by hand. It replaces an existing IMAGE_TAG= line (or appends one) and leaves every other
# line of .env, and the file's owner and permissions, as they were.
set -Eeuo pipefail

TAG="${1:?usage: deploy/record-image-tag.sh <image-tag>}"
ENV_FILE="${ENV_FILE:-.env}"

[[ "$TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]] || { echo "ERROR: '$TAG' is not a valid image tag." >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "ERROR: $ENV_FILE not found -- run from the repo root." >&2; exit 1; }

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
awk -v tag="$TAG" '
    /^IMAGE_TAG=/ { if (!done) print "IMAGE_TAG=" tag; done = 1; next }
    { print }
    END { if (!done) print "IMAGE_TAG=" tag }
' "$ENV_FILE" > "$tmp"
# cat into the existing file rather than mv: keeps its owner and (secret-file) permissions.
cat "$tmp" > "$ENV_FILE"
