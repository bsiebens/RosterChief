from django.conf import settings


def version(request):
    """The running platform version (settings.ROSTERCHIEF_VERSION, read from
    pyproject.toml) -- shown in the control panel header and the management/
    mobile footers so it's visible which build people are actually using.
    Global, not app-scoped: all three surfaces need it, and reading it is free."""
    return {"platform_version": settings.ROSTERCHIEF_VERSION}
