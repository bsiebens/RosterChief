"""Coach-mode screens for player evaluations -- filling in a club's rubric
for one player, and reviewing a player's past evaluations. Built on
evaluations.services' own reuse of formbuilder's dynamic-form engine (see
that module's docstring), the same "build_form + style_dynamic_form" idiom
mobile.views.FormFillView already uses for plain FormSends -- just backed by
evaluations.services.submit_evaluation instead of formbuilder's submit_form.

Access here is deliberately NOT CoachScopeMixin's usual can_manage_active_team
(a per-team management position) -- evaluations are a separate, additive,
club-wide grant (club.models.EvaluationManager, see club.services.access.
can_manage_evaluations's own docstring). Someone holding it may evaluate any
member club-wide, regardless of which team -- if any -- they personally
coach. CoachScopeMixin is still composed in below purely for the shared
dark-chrome shell's own context (team picker, ``me``, ...) and because the
natural way to reach these screens is still by browsing to a team's roster
first (CoachSquadView -> CoachRosterMemberView's own "Evaluations" row) --
that's a navigation convenience, not the access gate itself.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView
from waffle import flag_is_active

from club.services.access import can_manage_evaluations, current_season
from controlpanel.messages import notify
from evaluations.models import PlayerEvaluation
from evaluations.services import EvaluationRubricNotConfigured, EvaluationSubmissionError, current_rubric_form, submit_evaluation
from formbuilder.services.form_factory import build_form
from members.models import Member
from teams.models import TeamMembership

from .coach_mixins import CoachScopeMixin
from .forms import style_dynamic_form
from .views import _display_answer


class EvaluationAccessMixin:
    """404s the whole view when the "evaluations" waffle flag isn't active
    for this club, or 403s a signed-in account that isn't
    club.services.access.can_manage_evaluations -- mirrors club.mixins.
    EvaluationManagerRequiredMixin (the desktop app's own gate for the same
    grant), translated into this app's plain-dispatch idiom rather than
    UserPassesTestMixin, since every other Coach-mode view in this file
    already does its own gating that way (see e.g. CoachLineupPublishView's
    can_manage_active_team check) rather than via a test_func.

    Composed *after* LoginRequiredMixin (see each view below) so an
    anonymous visitor still gets the normal login redirect rather than a
    403/404 for a screen they haven't even tried to authenticate for yet;
    only once someone is signed in does this decide whether the feature
    exists for their club at all, and whether they're the right person for
    it.
    """

    def dispatch(self, request, *args, **kwargs):
        if not flag_is_active(request, "evaluations"):
            raise Http404("The “evaluations” feature isn't enabled for this club.")
        if not can_manage_evaluations(request.user, request.club):
            return HttpResponseForbidden()
        return super().dispatch(request, *args, **kwargs)


class CoachEvaluationMixin(CoachScopeMixin, LoginRequiredMixin, EvaluationAccessMixin):
    """Shared scaffolding for every evaluations screen: the gate above, plus
    resolving the player being evaluated. Kept separate from CoachScopeMixin's
    own get_membership()-style lookups (TeamMembership, scoped to whichever
    team is "active") since a can_manage_evaluations grant reaches any member
    of the club, not just this account's own roster -- get_player() below
    uses the same broad "attached to the club at all" check club.services.
    access.members_visible_to's own ADMIN branch uses, not a team-scoped one.
    """

    active_tab = "coach_squad"

    def get_player(self):
        club = self.request.club
        attached = Member.objects.filter(Q(member_of__club=club) | Q(team_memberships__team__club=club) | Q(staff_assignments__team__club=club) | Q(roles__club=club)).distinct()
        return get_object_or_404(attached, pk=self.kwargs["player_pk"])

    def get_membership(self, player):
        """This player's TeamMembership on the currently active team, if any --
        purely for the "back to the player's own Squad sheet" link (roster_member.html
        is reached by membership_pk, not player_pk). A can_manage_evaluations grant
        reaches players club-wide, so this is None whenever the player isn't on the
        active team's current roster (a club-wide evaluation manager browsing someone
        outside their own team) -- callers fall back to the plain Squad list link."""
        if self.active_team is None:
            return None
        return TeamMembership.objects.filter(team=self.active_team, member=player).select_related("member").first()


class CoachEvaluationHistoryView(CoachEvaluationMixin, TemplateView):
    """One player's past evaluations, most recent first (PlayerEvaluation's
    own default ordering) -- reached from the Squad screen's per-player
    detail sheet (roster_member.html's "Evaluations" row). Renders a "no
    evaluation form has been set up for this club yet" notice instead of a
    "New evaluation" button when the club has no current rubric
    (evaluations.services.current_rubric_form), rather than linking to a
    create screen that would only 404.
    """

    template_name = "mobile/coach/evaluation_history.html"
    screen_title = _("Evaluations")

    def get_context_data(self, **kwargs):
        player = self.get_player()
        evaluations = list(PlayerEvaluation.objects.filter(club=self.request.club, player=player).select_related("submission__member", "season"))
        return super().get_context_data(
            player=player,
            evaluations=evaluations,
            rubric_configured=current_rubric_form(self.request.club) is not None,
            membership=self.get_membership(player),
            **kwargs,
        )


class CoachEvaluationCreateView(CoachEvaluationMixin, TemplateView):
    """Fill in a new evaluation of one player against the club's current
    rubric -- see evaluations.services.submit_evaluation. The evaluator is
    always the signed-in account's own Member (self.me, resolved by
    CoachScopeMixin), never a field on the form.

    A rejected POST re-renders the same screen with the dynamic form's own
    field-level errors attached -- same idiom as mobile.views.FormFillView's
    own post(): re-running build_form's validation against the identical
    posted data reproduces EvaluationSubmissionError.errors' messages
    deterministically, so there's nothing to copy across by hand.
    """

    template_name = "mobile/coach/evaluation_form.html"
    screen_title = _("New evaluation")

    def get(self, request, *args, **kwargs):
        player = self.get_player()
        rubric_form = current_rubric_form(request.club)
        bound_form = style_dynamic_form(build_form(rubric_form)) if rubric_form is not None else None
        return self.render_to_response(self.get_context_data(player=player, rubric_configured=rubric_form is not None, form=bound_form))

    def post(self, request, *args, **kwargs):
        player = self.get_player()
        rubric_form = current_rubric_form(request.club)
        if rubric_form is None:
            raise Http404("This club hasn't set up an evaluation form yet.")

        try:
            submit_evaluation(club=request.club, player=player, season=current_season(request.club), evaluator=self.me, data=request.POST, files=request.FILES)
        except EvaluationRubricNotConfigured as error:
            raise Http404("This club hasn't set up an evaluation form yet.") from error
        except EvaluationSubmissionError as error:
            bound_form = style_dynamic_form(build_form(rubric_form, data=request.POST, files=request.FILES))
            bound_form.is_valid()
            notify(request, f"e|{_('Could not submit')}|{error}")
            return self.render_to_response(self.get_context_data(player=player, rubric_configured=True, form=bound_form))

        title = _("Evaluation submitted")
        body = _("Your evaluation of “%(player)s” has been recorded.") % {"player": player}
        notify(request, f"s|{title}|{body}")
        return HttpResponseRedirect(reverse("mobile:coach_evaluation_history", kwargs={"player_pk": player.pk}))


class CoachEvaluationDetailView(CoachEvaluationMixin, TemplateView):
    """Read-only "full answers" screen for one already-submitted evaluation
    -- reached by tapping a row on the history screen. Shows every field on
    the rubric Form the evaluation was actually scored against (not just
    Field.is_active ones), same "an evaluation keeps the exact questions it
    was scored against" reasoning as evaluations.models.PlayerEvaluation's
    own versioning docstring and mobile.views.FormResponseView's identical
    choice for a plain form Submission.
    """

    template_name = "mobile/coach/evaluation_detail.html"
    screen_title = _("Evaluation")

    def get_evaluation(self):
        return get_object_or_404(
            PlayerEvaluation.objects.filter(club=self.request.club, player_id=self.kwargs["player_pk"]).select_related("player", "season", "submission__member", "submission__send__form"),
            pk=self.kwargs["evaluation_pk"],
        )

    def get_context_data(self, **kwargs):
        evaluation = self.get_evaluation()
        answers_by_field_id = {answer.field_id: answer.value for answer in evaluation.submission.answers.all()}
        rows = [{"field": field, "display": _display_answer(answers_by_field_id.get(field.id))} for field in evaluation.submission.send.form.fields.order_by("order")]
        return super().get_context_data(player=evaluation.player, evaluation=evaluation, rows=rows, **kwargs)
