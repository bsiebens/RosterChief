"""Platform-wide switches: maintenance mode, the automated-email pause, and (read-only here) whether the push sandbox is on."""

from ninja import Router

from controlpanel import audit
from controlpanel.models import EmailSuppression, Maintenance

from ..schemas import EmailSuppressionIn, MaintenanceIn, MaintenanceState, ManualInvoicingState, PushSandboxState, SwitchesOut, SwitchState

router = Router(tags=["switches"])


def _state(row, cls=SwitchState, **extra):
    return cls(is_active=row.is_active, started_at=row.started_at, started_by_email=row.started_by.email if row.started_by_id else None, **extra)


def _all() -> SwitchesOut:
    from billing.services import invoicing_mode
    from notifications import sandbox

    m, e = Maintenance.current(), EmailSuppression.current()
    state = sandbox.get_state()
    manual = not invoicing_mode.auto_invoicing_enabled()
    return SwitchesOut(
        maintenance=_state(m, MaintenanceState, message=m.message),
        email_suppression=_state(e),
        push_sandbox=PushSandboxState(is_active=state["effective"], expires_at=state["expires_at"]),
        manual_invoicing=ManualInvoicingState(is_active=manual, pending_count=invoicing_mode.pending_count() if manual else 0),
    )


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
