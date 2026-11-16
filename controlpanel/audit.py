from .models import PlatformAuditLog


def record(actor, action: str, *, target=None, target_type: str = "", target_id="", club=None, data: dict | None = None) -> PlatformAuditLog:
    """Write an audit entry. ``target`` (any model instance) fills ``target_type``/``target_id``; ``club`` links a club."""
    if target is not None:
        target_type = target_type or target._meta.model_name
        target_id = target_id or str(target.pk)
    return PlatformAuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        club=club,
        data=data or {},
    )
