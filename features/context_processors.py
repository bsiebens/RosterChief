def features(request):
    """Makes ``{% if features.sponsoring %}`` work in templates."""
    value = getattr(request, "features", None)
    return {"features": value} if value is not None else {}
