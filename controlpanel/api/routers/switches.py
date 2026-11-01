"""Platform-wide switches: maintenance mode and the automated-email pause."""

from ninja import Router

from controlpanel import audit
from controlpanel.models import EmailSuppression, Maintenance

from ..schemas import EmailSuppressionIn, MaintenanceIn, MaintenanceState, SwitchesOut, SwitchState

router = Router(tags=["switches"])


def _state(row, cls=SwitchState, **extra):
    return cls(is_active=row.is_active, started_at=row.started_at, started_by_email=row.started_by.email if row.started_by_id else None, **extra)


def _all() -> SwitchesOut:
    m, e = Maintenance.current(), EmailSuppression.current()
    return SwitchesOut(maintenance=_state(m, MaintenanceState, message=m.message), email_suppression=_state(e))


@router.get("/switches", response=SwitchesOut, summary="Maintenance mode and the email pause")
def get_switches(request):
    return _all()


@router.put("/switches/maintenance", response=SwitchesOut, summary="Turn maintenance mode on (with a message) or off")
def put_maintenance(request, payload: MaintenanceIn):
    m = Maintenance.current()
    if payload.active:
        m.message = payload.message
    m.switch(payload.active, request.user)
    audit.record(request.user, "switch.maintenance_on" if payload.active else "switch.maintenance_off", target=m, data={"message": payload.message})
    return _all()


@router.put("/switches/email-suppression", response=SwitchesOut, summary="Pause or resume automated email (account email still goes out)")
def put_email_suppression(request, payload: EmailSuppressionIn):
    e = EmailSuppression.current()
    e.switch(payload.active, request.user)
    audit.record(request.user, "switch.email_paused" if payload.active else "switch.email_resumed", target=e)
    return _all()
