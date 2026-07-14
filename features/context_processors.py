from features.models import Maintenance


def maintenance(request):
    """So no control-panel page can forget the platform is closed. Cached, so it costs no
    query."""
    return {"maintenance_on": Maintenance.is_on()}
