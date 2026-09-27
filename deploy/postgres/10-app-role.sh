#!/usr/bin/env bash
# Runs once, from the postgres image's /docker-entrypoint-initdb.d, when the `db` volume is
# initialised for the first time (never again on an existing volume -- see DEPLOYMENT.md's
# "Database roles" for converting an existing database by hand).
#
# Creates the role the application connects as: a plain login role that owns the app database
# and its `public` schema (so `migrate` can create and alter tables) but is not a superuser and
# cannot create databases or roles. POSTGRES_USER stays the superuser, used only for this init
# and by deploy/backup.sh / deploy/restore-check.sh through `docker compose exec db`.
set -Eeo pipefail

if [ -z "${APP_DB_USER:-}" ] || [ -z "${APP_DB_PASSWORD:-}" ]; then
    echo "10-app-role.sh: APP_DB_USER and APP_DB_PASSWORD must be set (see .env.compose.example)." >&2
    exit 1
fi
if [ "$APP_DB_USER" = "$POSTGRES_USER" ]; then
    echo "10-app-role.sh: APP_DB_USER must differ from POSTGRES_USER (the superuser)." >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v app_user="$APP_DB_USER" -v app_password="$APP_DB_PASSWORD" -v app_db="$POSTGRES_DB" <<'SQL'
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER DATABASE :"app_db" OWNER TO :"app_user";
ALTER SCHEMA public OWNER TO :"app_user";
REVOKE ALL ON DATABASE :"app_db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"app_db" TO :"app_user";
SQL

echo "10-app-role.sh: created application role \"$APP_DB_USER\" owning database \"$POSTGRES_DB\"."
