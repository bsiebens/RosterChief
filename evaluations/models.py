from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from club.models import Season
from formbuilder.models import Form
from members.models import Member
from rosterchief.base import ClubScopedModel, validate_club_scope


class EvaluationChecklist(ClubScopedModel):
    """One named, independently-versioned rubric for this club (e.g. "U8",
    "U10", "Goalkeepers") -- a club can run several side by side, each
    scoring a different cohort of players against its own set of criteria.
    Formerly ``EvaluationSettings``, a singleton-per-club row; renamed and
    given a name/slug once evaluations grew to support more than one
    checklist per club.

    ``form`` points at the formbuilder.Form backing this checklist's
    *current* version, created lazily the first time an admin builds/edits
    its rubric (see evaluations.services.current_rubric_form). Swapping the
    rubric re-points ``form`` at a new Form (see
    evaluations.services.start_new_rubric_version) rather than mutating the
    existing one's Fields in place -- existing PlayerEvaluations keep
    referencing their Submission's original Form/Fields (already immutable
    once a Submission exists, via formbuilder's own FK shape), so an old
    evaluation still renders with the questions it was actually scored
    against. There is deliberately no FK from Form back to EvaluationChecklist
    (formbuilder stays unaware evaluations exist at all, same reasoning as
    evaluations.services' own module docstring) -- PlayerEvaluation.checklist
    is what lets a checklist's past versions be found again, by walking its
    own evaluations rather than Form.
    """

    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(_("slug"), blank=True)
    description = models.TextField(_("description"), blank=True, help_text=_("Optional notes for whoever picks a checklist -- e.g. which age group or squad it's meant for."))
    is_active = models.BooleanField(_("is active?"), default=True, help_text=_("Whether this checklist can still be picked for a new evaluation. Existing evaluations against it are unaffected."))
    form = models.ForeignKey(Form, on_delete=models.PROTECT, related_name="evaluation_checklists_for", null=True, blank=True, verbose_name=_("form"))

    slug_source = "name"

    class Meta:
        verbose_name = _("evaluation checklist")
        verbose_name_plural = _("evaluation checklists")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["club", "slug"], name="unique_evaluation_checklist_slug_per_club"),
        ]

    def __str__(self):
        return f"{self.club} - {self.name}"

    def clean(self):
        validate_club_scope(self, self.club_id, same_club_fields=("form",))


class PlayerEvaluation(ClubScopedModel):
    """The "who this was about, for which season" envelope around a
    formbuilder.Submission -- formbuilder's own Submission.member is the
    *submitter* (here, the evaluator filling the rubric in), and a generic
    form has no notion of a separate subject, so this pairs the two rather
    than growing Submission an evaluation-specific field. Club-wide, not
    scoped to a specific Team -- a member evaluated on more than one team in
    the same season gets one PlayerEvaluation per submission, same as any
    other coach's perspective (see the docstring on the missing uniqueness
    constraint below).

    Carries its own ``club`` FK (via ClubScopedModel) rather than relying on
    ``player``'s or ``submission``'s, even though both are already club-scoped
    transitively -- this is a first-class object management views query
    directly (every evaluation for this club), not just reached by joining
    through Form/FormSend the way formbuilder.Answer reaches Form.

    Access is gated entirely at the view layer (club.mixins.
    EvaluationManagerRequiredMixin) -- nothing here restricts who can create
    or query these; a player/guardian must simply never be routed to a view
    that does.
    """

    player = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="evaluations_received", verbose_name=_("player"))
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="player_evaluations", verbose_name=_("season"))
    #: Denormalized like ``club`` above, and for the same reason: this is what
    #: lets a checklist's evaluations be found across every version of its
    #: Form (each edit is a brand-new Form row -- see EvaluationChecklist's
    #: own docstring), and what the statistics/results-matrix/walkthrough
    #: views (evaluations.services) group and filter by. PROTECT -- a
    #: checklist with evaluations against it can be deactivated but not
    #: deleted out from under them.
    checklist = models.ForeignKey(EvaluationChecklist, on_delete=models.PROTECT, related_name="evaluations", verbose_name=_("checklist"))
    #: CASCADE: a PlayerEvaluation has no meaning once its own Submission (the
    #: evaluator's actual answers) is gone -- there is nothing left to show.
    submission = models.OneToOneField("formbuilder.Submission", on_delete=models.CASCADE, related_name="player_evaluation", verbose_name=_("submission"))

    class Meta:
        verbose_name = _("player evaluation")
        verbose_name_plural = _("player evaluations")
        ordering = ["-created"]
        # Deliberately no uniqueness constraint on (player, season) or (player,
        # season, evaluator) -- several coaches leaving their own evaluation of
        # the same player in the same season is a feature (multiple
        # perspectives), not a duplicate to prevent. A member can have zero,
        # one, or many PlayerEvaluations.

    def __str__(self):
        return f"{self.player} - {self.season}"

    def clean(self):
        validate_club_scope(self, self.club_id, member_fields=("player",), same_club_fields=("checklist",))
        if self.submission_id and self.submission.send.club_id != self.club_id:
            raise ValidationError({"submission": _("Must belong to the same club.")})


class EvaluationNote(ClubScopedModel):
    """A free-text discussion note about one player, in the context of one
    checklist -- captured during a walkthrough session ("transfer
    candidate", "work on positioning"), not tied to any single
    PlayerEvaluation. Deliberately separate from PlayerEvaluation's own
    structured rubric answers: this is the running log a coaching staff
    builds up across repeated walkthrough sessions, meant to still read back
    sensibly a month later regardless of how many more evaluations land in
    between -- an evaluation's answers are a snapshot at one point in time,
    a note is commentary that survives past it."""

    player = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="evaluation_notes", verbose_name=_("player"))
    checklist = models.ForeignKey(EvaluationChecklist, on_delete=models.CASCADE, related_name="notes", verbose_name=_("checklist"))
    #: SET_NULL, not PROTECT/CASCADE -- same reasoning as formbuilder.Submission.member
    #: (the evaluator on a Submission): whoever wrote the note may later leave the
    #: club, but the note itself -- and that someone left it -- should still show.
    author = models.ForeignKey(Member, on_delete=models.SET_NULL, related_name="evaluation_notes_authored", null=True, blank=True, verbose_name=_("author"))
    note = models.TextField(_("note"))

    class Meta:
        verbose_name = _("evaluation note")
        verbose_name_plural = _("evaluation notes")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.player} — {self.checklist} ({self.created:%Y-%m-%d})"

    def clean(self):
        validate_club_scope(self, self.club_id, member_fields=("player",), same_club_fields=("checklist",))
