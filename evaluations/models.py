from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from club.models import Season
from formbuilder.models import Form
from members.models import Member
from rosterchief.base import ClubScopedModel, validate_club_scope


class EvaluationSettings(ClubScopedModel):
    """Which formbuilder.Form is *the* current evaluation rubric for this club --
    one row per club (see the unique constraint below), created lazily the
    first time a club admin builds/edits their rubric (see
    evaluations.services.current_rubric_form).

    Swapping the rubric re-points ``form`` at a new Form (see
    evaluations.services.start_new_rubric_version) rather than mutating the
    existing one's Fields in place -- existing PlayerEvaluations keep
    referencing their Submission's original Form/Fields (already immutable
    once a Submission exists, via formbuilder's own FK shape), so an old
    evaluation still renders with the questions it was actually scored
    against.
    """

    form = models.ForeignKey(Form, on_delete=models.PROTECT, related_name="evaluation_settings_for", verbose_name=_("form"))

    class Meta:
        verbose_name = _("evaluation settings")
        verbose_name_plural = _("evaluation settings")
        constraints = [
            models.UniqueConstraint(fields=["club"], name="unique_evaluation_settings_per_club"),
        ]

    def __str__(self):
        return f"{self.club} evaluation settings"

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
        validate_club_scope(self, self.club_id, member_fields=("player",))
        if self.submission_id and self.submission.send.club_id != self.club_id:
            raise ValidationError({"submission": _("Must belong to the same club.")})
