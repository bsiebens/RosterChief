"""Markdown rendering for News.body.

Club staff author `body` as Markdown (see NewsForm's help text) -- the public
API (news/api.py) renders it to HTML on the way out; the control panel's own
preview shows the raw source as-authored, unrendered.

`nh3` (Rust/ammonia bindings) sanitizes the result: markdown.markdown() will
happily pass through raw HTML embedded in the source, and body is authored by
club staff, who aren't a fully trusted boundary for content served straight
into someone else's public website.
"""

import markdown as _markdown
import nh3
from django.utils.html import strip_tags
from django.utils.text import Truncator

_EXTENSIONS = [
    "nl2br",  # staff type in a plain textarea -- a single Enter should break the line,
    # not require a blank line like standard Markdown paragraphs do.
    "sane_lists",
    "fenced_code",
]

_ALLOWED_TAGS = {"p", "br", "strong", "em", "b", "i", "u", "a", "ul", "ol", "li", "blockquote", "code", "pre", "h2", "h3", "h4", "img", "hr"}
_ALLOWED_ATTRIBUTES = {"a": {"href", "title"}, "img": {"src", "alt", "title"}}
_ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def render_body_html(body: str) -> str:
    """Markdown source -> sanitized HTML."""
    html = _markdown.markdown(body, extensions=_EXTENSIONS)
    return nh3.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES, url_schemes=_ALLOWED_URL_SCHEMES)


def render_body_excerpt(body: str, *, words: int) -> str:
    """Plain-text excerpt, derived from the rendered HTML rather than the raw
    Markdown source -- otherwise syntax like `**`/`#`/`[text](url)` shows up
    verbatim in what's meant to be a short teaser."""
    plain_text = strip_tags(render_body_html(body))
    return Truncator(plain_text).words(words, truncate=" …")
