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
    """Raise ``ValueError`` unless ``url`` is a ``data:`` URL.

    The scheme rule on its own, without WeasyPrint -- pdf_url_fetcher() below applies
    it to every resource a render asks for.
    """
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise ValueError(f"Refusing to fetch non-data URL in PDF rendering: {scheme or 'relative'}:")


def pdf_url_fetcher():
    """A WeasyPrint ``URLFetcher`` that only resolves ``data:`` URLs.

    WeasyPrint (70+) expects a URLFetcher instance: it reads its ``_fail_on_errors``
    flag and wants its own response type back. Refused URLs raise ``ValueError``,
    which WeasyPrint turns into a warning and a skipped resource (fail_on_errors is
    off), never a failed render. ``allowed_protocols`` is WeasyPrint's own second
    check; redirects are off. Built on each call (cheap) rather than cached, and
    WeasyPrint is imported lazily, so importing this module never needs the native
    libraries.
    """
    from weasyprint import URLFetcher

    class DataOnlyURLFetcher(URLFetcher):
        def fetch(self, url, headers=None):
            safe_url_fetcher(url)
            return super().fetch(url, headers)

    return DataOnlyURLFetcher(allowed_protocols=ALLOWED_URL_SCHEMES, allow_redirects=False, fail_on_errors=False)
