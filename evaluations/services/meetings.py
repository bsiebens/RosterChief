"""Evaluation meetings: discussion notes, the session (open / end) and the decision recorded per player, plus the outcomes PDF.
Every write needs ``evaluations.execute_evaluations``."""

from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from evaluations.models import EvaluationChecklist, EvaluationNote, EvaluationOutcome, EvaluationSession
from members.models import Member
from members.services import access
from members.services.errors import Invalid, NotAllowed


def _member(actor) -> Member | None:
    return Member.objects.filter(user=actor).first()


def add_note(actor, checklist: EvaluationChecklist, player: Member, note: str) -> EvaluationNote:
    access.require_execute_evaluations(actor)
    note = (note or "").strip()
    if not note:
        raise Invalid("Check the note.", {"note": ["This field is required."]})
    return EvaluationNote.objects.create(checklist=checklist, player=player, author=_member(actor), note=note)


def delete_note(actor, note: EvaluationNote) -> None:
    """The author, or a checklist manager."""
    access.require_execute_evaluations(actor)
    if not ((note.author is not None and note.author.user_id == actor.pk) or access.can_manage_evaluations(actor)):
        raise NotAllowed("You may only delete your own notes.")
    note.delete()


def active_session(checklist: EvaluationChecklist) -> EvaluationSession | None:
    return EvaluationSession.objects.filter(checklist=checklist, ended_at__isnull=True).first()


def latest_session(checklist: EvaluationChecklist) -> EvaluationSession | None:
    """The newest meeting, open or ended -- what "download the outcomes of the last meeting" stays pointed at."""
    return EvaluationSession.objects.filter(checklist=checklist).order_by("-started_at").first()


def start_session(actor, checklist: EvaluationChecklist) -> EvaluationSession:
    """Begin a meeting; the open one is returned when there already is one (a double click can't start two)."""
    access.require_execute_evaluations(actor)
    existing = active_session(checklist)
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return EvaluationSession.objects.create(checklist=checklist, started_by=_member(actor))
    except IntegrityError:  # a concurrent start won the race
        return active_session(checklist)


def end_session(actor, session: EvaluationSession) -> EvaluationSession:
    access.require_execute_evaluations(actor)
    if session.is_open:
        session.ended_at = timezone.now()
        session.save(update_fields=["ended_at", "modified"])
    return session


def record_outcome(actor, session: EvaluationSession, player: Member, decision: str) -> EvaluationOutcome | None:
    """Save the decision on ``player`` in an open meeting; edited in place when one exists. A blank decision removes it."""
    access.require_execute_evaluations(actor)
    if not session.is_open:
        raise Invalid("This meeting has ended.", {"session": ["This meeting has ended."]}, code="session_ended")
    decision = (decision or "").strip()
    if not decision:
        EvaluationOutcome.objects.filter(session=session, player=player).delete()
        return None
    outcome, _created = EvaluationOutcome.objects.update_or_create(session=session, player=player, defaults={"decision": decision, "recorded_by": _member(actor)})
    return outcome


def session_outcomes(session: EvaluationSession) -> list[EvaluationOutcome]:
    """Every player decided on, by name. Only the decisions: never answers or notes (see ``EvaluationOutcome``)."""
    return list(session.outcomes.select_related("player").order_by("player__last_name", "player__first_name"))


def render_outcomes_pdf(session: EvaluationSession) -> bytes:
    from shop.services import invoices

    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise invoices.PDFUnavailable("PDF rendering is not available on this server.") from error
    club = invoices._club()
    html = render_to_string(
        "evaluations/outcomes.html",
        {"club": club, "session": session, "checklist": session.checklist, "outcomes": session_outcomes(session), "printed_at": timezone.localtime(), "logo_data_uri": invoices._logo_uri(club)},
    )
    return HTML(string=html).write_pdf()
