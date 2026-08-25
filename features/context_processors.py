from features.models import EmailSuppression, Maintenance


def maintenance(request):
    """So no control-panel page can forget the platform is closed, or that automated email
    is paused (features.models.EmailSuppression -- a narrower kill-switch than Maintenance,
    clubs stay reachable, only outbound mail stands down). Both cached, so this costs no
    query."""
    return {"maintenance_on": Maintenance.is_on(), "email_suppression_on": EmailSuppression.is_on()}
