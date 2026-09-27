"""Serving local-disk uploads (MEDIA_ROOT) through Django.

Used only while storage is local disk and no proxy in front serves /media/
itself (see rosterchief/urls.py). Uploaded files are user-supplied content on
the application's own origin, so every response carries the same headers the
Caddy media block sets: no content sniffing, a sandboxing CSP (a document
opened directly cannot run script or reach the page's cookies/origin), and SVG
forced to download rather than render inline.
"""

from django.views.static import serve

MEDIA_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"


def serve_media(request, path, document_root=None):
    response = serve(request, path, document_root=document_root)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = MEDIA_CSP
    if path.lower().endswith(".svg"):
        response["Content-Disposition"] = "attachment"
    return response
