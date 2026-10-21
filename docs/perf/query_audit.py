"""Query-count audit used for docs/PERFORMANCE.md. NOT wired into the app, CI or the test suite.

Run against a scratch database (never production):

    export DJANGO_SECRET_KEY=x DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS='*' \
           ROSTERCHIEF_BASE_DOMAIN=rosterchief.test DJANGO_DATABASE_URL=sqlite:////tmp/perf.sqlite3
    uv run python manage.py migrate
    # create a superuser + Member + Club(slug="demo"), then:
    uv run python manage.py seed_demo_data --club demo --admin-email <that user's email>
    uv run python manage.py shell < docs/perf/query_audit.py

For every management GET page that needs no arguments, and a few member-PWA pages, it prints the
number of SQL queries, how many were repeats of an already-seen query shape, and the three most
repeated shapes. ``STACKS`` additionally prints the calling file:line of each repeated query, which
is how the findings in PERFORMANCE.md were located. Query *counts* are portable to PostgreSQL;
timings measured on SQLite are not.
"""

import collections
import re
import traceback

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, get_resolver, reverse

import authentication.middleware as am

am.mfa_required_for = lambda user: False  # harness only: skip the MFA-enrolment redirect

HOST = "demo.rosterchief.test"
ADMIN = "admin@example.com"  # edit to match the account you created
PARENT = "anderson.parent@demo.rosterchief.app"  # created by seed_demo_data
SKIP = ("export", "pdf", "template", "delete", "send", "download", "ics", "clear", "mark", "approve", "import", "logout")
STACKS = True
U = get_user_model()


def shape(sql):
    return re.sub(r"\b\d+\b", "?", re.sub(r"'[^']*'", "?", sql))


def audit(client, path, label):
    client.get(path, HTTP_HOST=HOST)  # warm per-process caches (waffle flags, etc.)
    sites = collections.Counter()

    def wrapper(execute, sql, params, many, context):
        frames = [f for f in traceback.extract_stack() if "/RosterChief/" in f.filename and ".venv" not in f.filename and "query_audit" not in f.filename]
        where = " <- ".join(f"{f.filename.split('RosterChief/')[1]}:{f.lineno}" for f in reversed(frames[-2:]))
        sites[(shape(sql)[:60], where)] += 1
        return execute(sql, params, many, context)

    with CaptureQueriesContext(connection) as ctx, connection.execute_wrapper(wrapper):
        response = client.get(path, HTTP_HOST=HOST)
    shapes = collections.Counter(shape(q["sql"]) for q in ctx.captured_queries)
    repeats = sum(n - 1 for n in shapes.values() if n > 1)
    print(f"{len(ctx.captured_queries):4d} queries  {repeats:3d} repeats  {response.status_code}  {label}")
    if STACKS:
        for (sql, where), n in sites.most_common(3):
            if n > 1:
                print(f"       {n:3d}x {sql}  @ {where}")


admin = Client()
admin.force_login(U.objects.get(email=ADMIN))
parent = Client()
parent.force_login(U.objects.get(email=PARENT))

for pattern in get_resolver().namespace_dict["management"][1].url_patterns:
    name = getattr(pattern, "name", None)
    if not name or any(word in name for word in SKIP):
        continue
    try:
        audit(admin, reverse(f"management:{name}"), f"management:{name}")
    except NoReverseMatch:
        pass

audit(admin, reverse("management:member_list") + "?search=an", "management:member_list?search")
for name in ("mobile:home", "mobile:calendar", "mobile:news_list", "mobile:me", "mobile:payments", "mobile:forms_list"):
    audit(parent, reverse(name), name)
