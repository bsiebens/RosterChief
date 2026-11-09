"""Player evaluations. Lives in the club schema, so there is no club foreign key anywhere.

A checklist is a named rubric whose criteria are the fields of a ``formbuilder.Form`` (see ``evaluations.services.checklists``).
Filling it in for a player stores an ordinary formbuilder ``Submission`` (the player is ``member``, the evaluator
``submitted_by``) plus the ``PlayerEvaluation`` envelope that says which checklist and season it was about.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from events.models import Season
from formbuilder.models import Form, unique_slug
from members.models import Member
from rosterchief.base import UUIDModel


class EvaluationChecklist(UUIDModel):
    """One named rubric (e.g. "U8", "Goalkeepers"). A club can run several side by side, each scoring its own cohort against
    its own criteria. The criteria are the fields of ``form``; that form is created with the checklist and never shown in the
    Forms section."""

    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=255, unique=True, blank=True)
    description = models.TextField(_("description"), blank=True, help_text=_("Optional notes for whoever picks a checklist, e.g. which age group it is meant for."))
    is_active = models.BooleanField(_("is active?"), default=True, help_text=_("Whether this checklist can still be picked for a new evaluation. Existing evaluations are unaffected."))
    form = models.OneToOneField(Form, on_delete=models.PROTECT, related_name="evaluation_checklist", verbose_name=_("form"))

    class Meta:
        verbose_name = _("evaluation checklist")
        verbose_name_plural = _("evaluation checklists")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name, fallback="checklist")
        super().save(*args, **kwargs)


class PlayerEvaluation(UUIDModel):
    """Who a submission was about, on which checklist and in which season. Several coaches may evaluate the same player in the
    same season (several perspectives), so there is deliberately no uniqueness constraint."""

    player = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="evaluations_received", verbose_name=_("player"))
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="player_evaluations", verbose_name=_("season"))
    #: PROTECT: a checklist with evaluations can be deactivated but not deleted out from under them.
    checklist = models.ForeignKey(EvaluationChecklist, on_delete=models.PROTECT, related_name="evaluations", verbose_name=_("checklist"))
    #: CASCADE: without its submission (the evaluator's answers) there is nothing left to show.
    submission = models.OneToOneField("formbuilder.Submission", on_delete=models.CASCADE, related_name="player_evaluation", verbose_name=_("submission"))

    class Meta:
        verbose_name = _("player evaluation")
        verbose_name_plural = _("player evaluations")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.player} - {self.season}"

    @property
    def evaluator(self):
        return self.submission.submitted_by


class EvaluationNote(UUIDModel):
    """A free-text discussion note about one player in the context of one checklist, left during a meeting ("transfer candidate",
    "work on positioning"). Not tied to any one evaluation: a running log that still reads sensibly a month later."""

    player = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="evaluation_notes", verbose_name=_("player"))
    checklist = models.ForeignKey(EvaluationChecklist, on_delete=models.CASCADE, related_name="notes", verbose_name=_("checklist"))
    #: SET_NULL: whoever wrote it may leave the club; the note, and that someone left it, should stay.
    author = models.ForeignKey(Member, on_delete=models.SET_NULL, related_name="evaluation_notes_authored", null=True, blank=True, verbose_name=_("author"))
    note = models.TextField(_("note"))

    class Meta:
        verbose_name = _("evaluation note")
        verbose_name_plural = _("evaluation notes")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.player} - {self.checklist} ({self.created:%Y-%m-%d})"


class EvaluationSession(UUIDModel):
    """One evaluation meeting for a checklist: started when the coaching staff sit down to go through players together, ended when
    it wraps up. It exists so outcomes have something to attach to. At most one is open per checklist at a time."""

    checklist = models.ForeignKey(EvaluationChecklist, on_delete=models.CASCADE, related_name="sessions", verbose_name=_("checklist"))
    started_at = models.DateTimeField(_("started at"), auto_now_add=True)
    ended_at = models.DateTimeField(_("ended at"), null=True, blank=True)
    started_by = models.ForeignKey(Member, on_delete=models.SET_NULL, related_name="+", null=True, blank=True, verbose_name=_("started by"))

    class Meta:
        verbose_name = _("evaluation session")
        verbose_name_plural = _("evaluation sessions")
        ordering = ["-started_at"]
        constraints = [models.UniqueConstraint(fields=["checklist"], condition=models.Q(ended_at__isnull=True), name="one_open_evaluation_session_per_checklist")]

    def __str__(self):
        return f"{self.checklist} - {self.started_at:%Y-%m-%d %H:%M}"

    @property
    def is_open(self) -> bool:
        return self.ended_at is None


class EvaluationOutcome(UUIDModel):
    """The meeting's decision on one player (promoted, released, needs another look ...), kept apart from the private notes so
    exporting "what we decided" can never pull the informal notes along. One row per (session, player), edited in place."""

    session = models.ForeignKey(EvaluationSession, on_delete=models.CASCADE, related_name="outcomes", verbose_name=_("session"))
    player = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="evaluation_outcomes", verbose_name=_("player"))
    decision = models.TextField(_("decision"))
    recorded_by = models.ForeignKey(Member, on_delete=models.SET_NULL, related_name="+", null=True, blank=True, verbose_name=_("recorded by"))

    class Meta:
        verbose_name = _("evaluation outcome")
        verbose_name_plural = _("evaluation outcomes")
        ordering = ["player__last_name", "player__first_name"]
        constraints = [models.UniqueConstraint(fields=["session", "player"], name="unique_outcome_per_session_per_player")]

    def __str__(self):
        return f"{self.player} - {self.session}"
