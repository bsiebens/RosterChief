"""The online developer docs for the app API, at /api/docs/ on the base domain.

Rendered from Markdown in ``docs/app-api/`` so the guide lives in the repository next to
the code it describes (and is reviewed with it). Public on purpose, like the public API's
own /api/v1/docs: nothing here is secret, and app developers need it without an account.
The API references themselves are the live OpenAPI pages it links to.
"""

import functools
from pathlib import Path

import markdown
from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from rosterchief.admin_host import is_tenant_host

DOCS_DIR = Path(settings.BASE_DIR) / "docs" / "app-api"

#: URL slug -> (file, navigation title). The only files ever served: no path from the URL
#: reaches the filesystem.
PAGES = {
    "": ("index.md", _("Overview")),
    "ios": ("ios.md", _("iOS guide")),
    "errors": ("errors.md", _("Errors")),
    "status": ("status.md", _("Status")),
}


@functools.cache
def _render(filename: str) -> str:
    text = (DOCS_DIR / filename).read_text(encoding="utf-8")
    return markdown.markdown(text, extensions=["fenced_code", "tables", "toc"])


def _base_domain_url(request) -> str:
    """This URL on the base domain (same scheme and port): the docs belong to the platform,
    not to a club, but a developer is often on a club host when they go looking."""
    host, _colon, port = request.get_host().partition(":")
    base = settings.ROSTERCHIEF_BASE_DOMAIN or host
    # The port the client used (in the Host header), not the one the server listens on.
    suffix = f":{port}" if port and port not in ("80", "443") else ""
    return f"{request.scheme}://{base}{suffix}{request.get_full_path()}"


def docs_page(request, page=""):
    if page not in PAGES:
        raise Http404("No such page.")
    if is_tenant_host(request):
        return redirect(_base_domain_url(request))
    filename, title = PAGES[page]
    html = _render(filename) if not settings.DEBUG else _render.__wrapped__(filename)
    nav = [{"href": "/api/docs/" + (slug + "/" if slug else ""), "title": nav_title, "current": slug == page} for slug, (_file, nav_title) in PAGES.items()]
    return render(request, "appapi/docs.html", {"title": title, "body": mark_safe(html), "nav": nav})
