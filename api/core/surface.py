"""Which URLs under /api/v1/ are public and which are protected.

One NinjaAPI serves both (api/v1.py). Middleware that must treat them differently -- CORS,
Cache-Control -- can't ask ninja per request (the operation is only known after routing, and a
preflight never reaches it), so ``api.v1`` registers its public routers here and this module
answers from their path templates.
"""

import re

#: Documentation and spec pages ninja serves for the whole API; public, like the spec itself.
DOC_PATHS = ("/api/v1/docs", "/api/v1/openapi.json")

_PARAM = re.compile(r"\{(?:(path):)?\w+\}|\{\w+:\w+\}")
_patterns: list[re.Pattern] = []


def _template_to_regex(template: str) -> re.Pattern:
    out, last = "", 0
    for match in _PARAM.finditer(template):
        out += re.escape(template[last : match.start()])
        out += ".+" if match.group(1) == "path" else "[^/]+"
        last = match.end()
    return re.compile(out + re.escape(template[last:]))


def register_public(routers) -> None:
    """``routers``: ``[(prefix, ninja Router)]`` whose routes need no sign-in."""
    _patterns.clear()
    for prefix, router in routers:
        for path in router.path_operations:
            _patterns.append(_template_to_regex("/api/v1/" + (prefix.strip("/") + "/" + path.lstrip("/")).lstrip("/")))


def is_public_path(path: str) -> bool:
    return path.startswith(DOC_PATHS) or any(pattern.fullmatch(path) for pattern in _patterns)
