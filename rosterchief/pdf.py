"""Shared WeasyPrint helpers.

Every PDF in the app is rendered from a Django template with ``HTML(string=...)``.
WeasyPrint's default URL fetcher follows ``file://``, ``http://`` and ``https://``
references it finds in that HTML (``<img src>``, ``<link href>``, CSS ``url()``,
``@import``, ``@font-face``), so any user-controlled text that ends up in a template
could make the server read local files or reach internal hosts while rendering.

None of the PDF templates need an external resource: logos and QR codes are
embedded as ``data:`` URIs and all styling is an inline ``<style>`` block. So the
fetcher passed to WeasyPrint only allows ``data:`` URLs; anything else raises,
which WeasyPrint catches, logs as a warning, and skips (the resource is simply
left out of the PDF).

WeasyPrint is only imported inside the fetcher, after the scheme check -- same
lazy-import rule as the ``render_pdf`` helpers that use it (native pango/cairo
libraries must not be required just to import the app).
"""

from urllib.parse import urlsplit

ALLOWED_URL_SCHEMES = frozenset({"data"})


def safe_url_fetcher(url, *args, **kwargs):
    """WeasyPrint ``url_fetcher`` that only resolves ``data:`` URLs.

    Raises ``ValueError`` for every other scheme (``file``, ``http``, ``https``,
    relative URLs, ...); WeasyPrint turns that into a skipped resource rather than a
    failed render.
    """
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise ValueError(f"Refusing to fetch non-data URL in PDF rendering: {scheme or 'relative'}:")

    from weasyprint import URLFetcher

    # allowed_protocols is a second check inside WeasyPrint itself; redirects can't
    # apply to data: URLs, but are switched off so no future scheme change can follow one.
    return URLFetcher(allowed_protocols=ALLOWED_URL_SCHEMES, allow_redirects=False).fetch(url)
