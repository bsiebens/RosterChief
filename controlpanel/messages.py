"""A compact way to queue a Django message that carries its own title.

Django's messages framework has no title field — a call site that wants one passes it
as ``extra_tags`` (``messages.success(request, body, extra_tags="Club created")``), which
reads fine written out but is easy to forget, so in practice every message ends up on
the generic per-level heading (`as_alert`'s "Done" / "Careful" / "Something went wrong").

``notify`` folds level, title and body into one string instead: ``"<level>|<title>|<body>"``.
One call, title included, nothing to forget. `as_alert` (controlpanel/templatetags/ui.py)
reads the title back off ``extra_tags`` at render time — unchanged from before.
"""

from django.contrib import messages

#: One letter per Django message level. `notify` picks the level from the spec string;
#: `as_alert` picks the icon/colour/fallback-title from the level the message actually
#: carries (via ``level_tag``), so the two stay in step by construction.
LEVELS = {
    "s": messages.SUCCESS,
    "i": messages.INFO,
    "w": messages.WARNING,
    "e": messages.ERROR,
    "d": messages.DEBUG,
}


def notify(request, spec: str, **kwargs) -> None:
    """Queue a message from a ``"<level>|<title>|<body>"`` spec.

    ``level`` is one of ``s`` (success), ``i`` (info), ``w`` (warning), ``e`` (error),
    ``d`` (debug). An empty title (``"s||Body text"``) falls back to the generic
    per-level heading, same as never passing ``extra_tags`` at all.
    """
    level_code, title, body = spec.split("|", 2)
    messages.add_message(request, LEVELS[level_code], body, extra_tags=title, **kwargs)
