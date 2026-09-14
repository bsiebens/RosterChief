"""The public, unauthenticated tournament-registration flow -- a visiting
(foreign) team applies to enter a club's home tournament from a link on the
club's own site, then checks on its application later via an unguessable
token. Mirrors registration.views' own RegistrationView/RegistrationStatusView
shape closely (two views, one form-then-confirm page and one token-gated
status page), minus the price-preview round trip that app needs and this one
doesn't -- a tournament entry has nothing to price.

The host club's OWN team skips this entirely via a staff-side shortcut
(TournamentTeam created directly by staff, registration left null) -- see
TournamentRegistration's own docstring. This form only ever collects
foreign-team fields, and only ever creates PENDING registrations: deciding
one (approve/reject) is tournaments.services.registration's job, called from
the staff-side review queue, never from here.
"""

import itertools

from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView, ListView

from club.mixins import FeatureFlagMixin
from controlpanel.messages import notify
from members.views import ClubScopedPublicMixin
from news.services import render_body_html

from .forms import TournamentRegistrationForm, TournamentRegistrationPlayerFormSet, players_from_formset
from .models import Match, Tournament, TournamentRegistration, TournamentRegistrationPlayer, TournamentStage, TournamentTeam
from .services.notifications import send_registration_confirmation_email
from .services.scheduling import compute_standings

#: Tournament.Status values still open to new public entries. Once a
#: tournament is under way, finished, or cancelled, its registration page
#: simply doesn't exist any more (404) -- same "the section doesn't exist
#: here" reasoning FeatureFlagMixin itself uses for a disabled club, rather
#: than a "closed" message on an otherwise-live page.
OPEN_FOR_REGISTRATION_STATUSES = (Tournament.Status.DRAFT, Tournament.Status.PUBLISHED)


class TournamentFeatureMixin(FeatureFlagMixin, ClubScopedPublicMixin):
    """Shared gating for every public tournament view -- layers
    FeatureFlagMixin onto ClubScopedPublicMixin the same way club/mixins.py's
    own OfficialsStaffRequiredMixin layers it onto a management-side tier, so
    a club with "tournaments" off doesn't just lose its staff tooling; its
    public registration URLs actually stop resolving too (unlike
    formbuilder's public view, which FeatureFlagMixin's own docstring calls
    out as the gap not to repeat)."""

    feature_flag = "tournaments"


class TournamentRegistrationView(TournamentFeatureMixin, View):
    template_name = "tournaments/register.html"

    def get_tournament(self, request, tournament_slug):
        tournament = get_object_or_404(Tournament, club=request.club, slug=tournament_slug)
        if tournament.status not in OPEN_FOR_REGISTRATION_STATUSES:
            raise Http404("This tournament isn't accepting entries.")
        return tournament

    def get_forms(self, data=None, files=None):
        contact_form = TournamentRegistrationForm(data, files)
        player_formset = TournamentRegistrationPlayerFormSet(data, prefix="players")
        return contact_form, player_formset

    def render_page(self, request, tournament, contact_form, player_formset):
        return render(request, self.template_name, {"tournament": tournament, "form": contact_form, "player_formset": player_formset})

    def get(self, request, *args, **kwargs):
        tournament = self.get_tournament(request, kwargs["tournament_slug"])
        if not tournament.registration_open:
            return render(request, self.template_name, {"tournament": tournament, "registration_closed": True})
        contact_form, player_formset = self.get_forms()
        return self.render_page(request, tournament, contact_form, player_formset)

    def post(self, request, *args, **kwargs):
        tournament = self.get_tournament(request, kwargs["tournament_slug"])
        if not tournament.registration_open:
            return render(request, self.template_name, {"tournament": tournament, "registration_closed": True})
        contact_form, player_formset = self.get_forms(request.POST, request.FILES)
        if not contact_form.is_valid() or not player_formset.is_valid():
            return self.render_page(request, tournament, contact_form, player_formset)

        # status is left at its PENDING default -- deciding a registration is
        # tournaments.services.registration's job (the staff-side review
        # queue), never this view's.
        registration = contact_form.save(commit=False)
        registration.tournament = tournament
        registration.save()

        TournamentRegistrationPlayer.objects.bulk_create([TournamentRegistrationPlayer(registration=registration, **player) for player in players_from_formset(player_formset)])

        send_registration_confirmation_email(registration, request=request)
        notify(request, f"s|{_('Registration received')}|{_('Thanks -- the club will review this and be in touch. A confirmation email is on its way with a link to check status.')}")
        return redirect("tournaments:registration_status", tournament_slug=tournament.slug, token=registration.status_token)


class TournamentRegistrationStatusView(TournamentFeatureMixin, View):
    """The link tournaments.services.notifications.send_registration_confirmation_email
    hands out -- unguessable-token access (TournamentRegistration.status_token,
    same idea as registration.models.RegistrationBatch.status_token), since
    whoever submitted the registration may have no account at all.

    The roster is editable from here while the registration is still
    PENDING -- a team often registers before its final line-up is settled,
    and making them email the club to fix a name or add a late signing only
    adds a manual step for staff. Once approved, TournamentRosterEntry is
    already snapshotted from this data (tournaments.services.registration.
    approve_registration) and editing here wouldn't reach it any more; once
    rejected, there's nothing left to edit. Everything else (contact
    details, team identity/colors/logo) stays fixed here -- deliberately
    out of scope, same as before.
    """

    template_name = "tournaments/registration_status.html"

    def get_registration(self, request, tournament_slug, token):
        return get_object_or_404(
            TournamentRegistration.objects.select_related("tournament"),
            tournament__club=request.club,
            tournament__slug=tournament_slug,
            status_token=token,
        )

    def get_player_formset(self, registration, data=None):
        initial = [{"first_name": player.first_name, "last_name": player.last_name, "jersey_number": player.jersey_number} for player in registration.players.all()]
        return TournamentRegistrationPlayerFormSet(data, initial=initial, prefix="players")

    def render_page(self, request, registration, player_formset=None):
        context = {"tournament": registration.tournament, "registration": registration}
        if registration.status == TournamentRegistration.Status.PENDING:
            context["player_formset"] = player_formset if player_formset is not None else self.get_player_formset(registration)
        return render(request, self.template_name, context)

    def get(self, request, *args, **kwargs):
        registration = self.get_registration(request, kwargs["tournament_slug"], kwargs["token"])
        return self.render_page(request, registration)

    def post(self, request, *args, **kwargs):
        registration = self.get_registration(request, kwargs["tournament_slug"], kwargs["token"])
        if registration.status != TournamentRegistration.Status.PENDING:
            notify(request, f"e|{_('Could not update roster')}|{_('This entry has already been decided -- the roster can no longer be changed.')}")
            return redirect("tournaments:registration_status", tournament_slug=registration.tournament.slug, token=registration.status_token)

        player_formset = self.get_player_formset(registration, data=request.POST)
        if not player_formset.is_valid():
            return self.render_page(request, registration, player_formset=player_formset)

        registration.players.all().delete()
        TournamentRegistrationPlayer.objects.bulk_create([TournamentRegistrationPlayer(registration=registration, **player) for player in players_from_formset(player_formset)])

        notify(request, f"s|{_('Roster updated')}|{_('Your roster has been updated.')}")
        return redirect("tournaments:registration_status", tournament_slug=registration.tournament.slug, token=registration.status_token)


# --------------------------------------------------------------------------
# Public tournament site -- home page, schedule, standings/bracket, one
# team's own slate. No login, no unguessable token (unlike the registration
# flow above): a tournament's schedule/standings are public information by
# design, same reasoning TournamentTeam.slug's own docstring already gives
# for *that* not being a secret either.
# --------------------------------------------------------------------------

#: Tournament.Status values shown on the public tournament list
#: (TournamentListView). Deliberately narrower than registration's own
#: OPEN_FOR_REGISTRATION_STATUSES:
#: - DRAFT is excluded -- a tournament can legitimately be quietly open for
#:   entries (see OPEN_FOR_REGISTRATION_STATUSES above) before the club is
#:   ready to broadcast that it exists at all; the direct registration link
#:   handles that case, the public list shouldn't.
#: - CANCELLED is excluded too -- once a tournament isn't happening, there's
#:   no ongoing reason to keep it in a browsable list; a club that wants to
#:   announce a cancellation does so through news/direct communication, not
#:   by leaving a dead entry in this listing.
#: PUBLISHED and IN_PROGRESS are shown as "upcoming/in progress"; COMPLETED
#: becomes "past" -- see TournamentListView.get_context_data.
PUBLIC_LIST_STATUSES = (Tournament.Status.PUBLISHED, Tournament.Status.IN_PROGRESS, Tournament.Status.COMPLETED)


def _round_groups(matches) -> list[dict]:
    """Turns an ordered (by round_number, then sequence) iterable of Match
    rows into a ``[{round_number, label, matches}, ...]`` list -- shared by
    every grouped-by-round view below (the full schedule, the schedule rows
    partial, a bracket-format stage's standings view). Mirrors
    ``management.views.TournamentScheduleView.get_context_data``'s own
    ``itertools.groupby`` idiom exactly, factored out so the public views
    below and that staff-only one can never quietly drift apart on what
    "grouped by round" means."""
    rounds = []
    for round_number, round_matches in itertools.groupby(matches, key=lambda match: match.round_number):
        round_matches = list(round_matches)
        label = next((match.round_label for match in round_matches if match.round_label), "") or (_("Round %(number)d") % {"number": round_number})
        rounds.append({"round_number": round_number, "label": label, "matches": round_matches})
    return rounds


def _grouped_schedule(tournament) -> list[dict]:
    """Every match across every stage of ``tournament``, grouped stage then
    round -- the read-only public counterpart to the staff-side
    ``management.views.TournamentScheduleView``'s own grouping, reused by
    both the full schedule page and its htmx-polled rows partial so they can
    never disagree about the grouping."""
    matches = Match.objects.filter(stage__tournament=tournament).select_related("stage", "group", "field", "home_team", "away_team").order_by("stage__sequence", "round_number", "sequence")
    return [{"stage": stage, "rounds": _round_groups(list(stage_matches))} for stage, stage_matches in itertools.groupby(matches, key=lambda match: match.stage)]


def _standings_context(tournament) -> dict:
    """Every stage's standings/progression, split into two shapes a template
    can walk directly:

    - ``standings_stages``: round-robin/Swiss/custom stages get one table
      (``group`` is ``None``); a GROUPS-format stage gets one table per
      ``TournamentGroup``, each computed via ``compute_standings(stage,
      group=group)`` -- exactly the per-group call the design spec asks for.
    - ``bracket_stages``: single/double-elimination stages instead get a
      round-by-round match list (``_round_groups``, the same shape the
      schedule view uses) -- a table of points/wins doesn't mean anything
      for a knockout stage, but "who's advanced so far" does, and
      ``Match.home_display``/``away_display`` already render an unresolved
      slot as "TBD".

    Shared by both the full standings page and its htmx-polled rows partial,
    and reused (truncated in the template) for TournamentDetailView's own
    compact snapshot.
    """
    standings_stages, bracket_stages = [], []
    for stage in tournament.stages.order_by("sequence").prefetch_related("groups"):
        if stage.format == TournamentStage.Format.GROUPS:
            groups = list(stage.groups.order_by("sequence"))
            tables = [{"group": group, "rows": compute_standings(stage, group=group)} for group in groups]
            standings_stages.append({"stage": stage, "tables": tables})
        elif stage.format in (TournamentStage.Format.SINGLE_ELIM, TournamentStage.Format.DOUBLE_ELIM):
            matches = list(stage.matches.select_related("home_team", "away_team").order_by("round_number", "sequence"))
            bracket_stages.append({"stage": stage, "rounds": _round_groups(matches)})
        else:
            # ROUND_ROBIN, SWISS, CUSTOM -- one plain table. compute_standings
            # only ever reads completed matches, so this is harmless (an
            # empty table) even for a CUSTOM stage nobody's populated yet.
            standings_stages.append({"stage": stage, "tables": [{"group": None, "rows": compute_standings(stage)}]})
    return {"standings_stages": standings_stages, "bracket_stages": bracket_stages}


class TournamentListView(TournamentFeatureMixin, ListView):
    """``/tournaments/`` -- every publicly-listable tournament for this club,
    most recent first, split into "upcoming/in progress" vs. "past" in the
    template. See ``PUBLIC_LIST_STATUSES`` for exactly which statuses that
    is and why."""

    model = Tournament
    template_name = "tournaments/tournament_list.html"
    context_object_name = "tournaments"

    def get_queryset(self):
        return Tournament.objects.filter(club=self.request.club, status__in=PUBLIC_LIST_STATUSES)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tournaments = context["tournaments"]
        context["upcoming_tournaments"] = [tournament for tournament in tournaments if tournament.status != Tournament.Status.COMPLETED]
        context["past_tournaments"] = [tournament for tournament in tournaments if tournament.status == Tournament.Status.COMPLETED]
        return context


class TournamentDetailView(TournamentFeatureMixin, DetailView):
    """``/tournaments/<slug>/`` -- the tournament "home": dates, format per
    stage, a compact standings/bracket snapshot, and a short upcoming-matches
    teaser (the full schedule lives at ``TournamentScheduleView``, linked
    from here).

    Unlike ``TournamentListView``, this does *not* filter by
    ``PUBLIC_LIST_STATUSES`` -- only by ``club``, so any status (including a
    DRAFT tournament whose direct registration link is already being shared,
    or a CANCELLED one someone still has bookmarked) resolves for whoever
    has the exact URL. Judgment call: unlike ``TournamentRegistration.status_token``,
    a tournament's own slug was never meant to be a secret (see
    ``TournamentTeam.slug``'s docstring making the same point for a team's
    own page) -- "not broadcast on the public list" and "doesn't exist" are
    different things, and staff sharing a not-yet-listed tournament's link
    directly (the same reason DRAFT still accepts registrations) is a
    legitimate, intentional use this view shouldn't block.
    """

    model = Tournament
    template_name = "tournaments/tournament_detail.html"
    context_object_name = "tournament"

    def get_queryset(self):
        return Tournament.objects.filter(club=self.request.club)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tournament = self.object
        context["stages"] = list(tournament.stages.order_by("sequence"))
        context["upcoming_matches"] = (
            Match.objects.filter(stage__tournament=tournament)
            .exclude(status=Match.Status.COMPLETE)
            .exclude(scheduled_start__isnull=True)
            .select_related("stage", "home_team", "away_team")
            .order_by("scheduled_start")[:5]
        )
        context["description_html"] = render_body_html(tournament.description) if tournament.description else ""
        context.update(_standings_context(tournament))
        return context


class TournamentScheduleView(TournamentFeatureMixin, DetailView):
    """``/tournaments/<slug>/schedule/`` -- the full schedule, every match
    across every stage/round, grouped the same way the staff-only
    ``management.views.TournamentScheduleView`` groups them (minus the
    inline edit forms -- this is read-only). The rows themselves live in
    ``tournaments/_schedule_rows.html``, included here and polled by
    ``TournamentScheduleRowsView`` below for live score updates."""

    model = Tournament
    template_name = "tournaments/tournament_schedule.html"
    context_object_name = "tournament"

    def get_queryset(self):
        return Tournament.objects.filter(club=self.request.club)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["stages"] = _grouped_schedule(self.object)
        return context


class TournamentScheduleRowsView(TournamentFeatureMixin, DetailView):
    """``/tournaments/<slug>/schedule/rows/`` -- just the grouped match table
    (``_schedule_rows.html``), no page chrome. Polled by the full schedule
    page's own ``hx-get``/``hx-trigger="every 20s"`` container -- htmx
    polling, not WebSockets/SSE (there's no such infrastructure anywhere in
    this codebase, see the design spec), and only the rows are re-fetched,
    never a full-page render."""

    model = Tournament
    template_name = "tournaments/_schedule_rows.html"
    context_object_name = "tournament"

    def get_queryset(self):
        return Tournament.objects.filter(club=self.request.club)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["stages"] = _grouped_schedule(self.object)
        return context


class TournamentStandingsView(TournamentFeatureMixin, DetailView):
    """``/tournaments/<slug>/standings/`` -- every stage's standings (a table
    per stage, or per group within a GROUPS-format stage), plus a simple
    bracket/progression view for single/double-elimination stages. See
    ``_standings_context`` for the exact shape. The rows themselves live in
    ``tournaments/_standings_rows.html``, included here and polled by
    ``TournamentStandingsRowsView`` below."""

    model = Tournament
    template_name = "tournaments/tournament_standings.html"
    context_object_name = "tournament"

    def get_queryset(self):
        return Tournament.objects.filter(club=self.request.club)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_standings_context(self.object))
        return context


class TournamentStandingsRowsView(TournamentFeatureMixin, DetailView):
    """``/tournaments/<slug>/standings/rows/`` -- just the standings/bracket
    tables (``_standings_rows.html``), no page chrome. Same
    ``hx-trigger="every 20s"`` polling idiom as ``TournamentScheduleRowsView``."""

    model = Tournament
    template_name = "tournaments/_standings_rows.html"
    context_object_name = "tournament"

    def get_queryset(self):
        return Tournament.objects.filter(club=self.request.club)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_standings_context(self.object))
        return context


class TournamentTeamScheduleView(TournamentFeatureMixin, DetailView):
    """``/tournaments/<slug>/teams/<team_slug>/`` -- one ``TournamentTeam``'s
    own matches only (``Q(home_team=team) | Q(away_team=team)``), scoped by
    both the tournament's own slug and the team's own slug so a team slug
    that happens to collide with one from a different tournament (slugs are
    unique *per tournament*, not globally -- see ``TournamentTeam.slug``'s
    own docstring) can never resolve the wrong team."""

    model = TournamentTeam
    template_name = "tournaments/tournament_team_schedule.html"
    context_object_name = "team"

    def get_object(self, queryset=None):
        return get_object_or_404(
            TournamentTeam.objects.select_related("tournament"),
            tournament__club=self.request.club,
            tournament__slug=self.kwargs["slug"],
            slug=self.kwargs["team_slug"],
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        team = self.object
        context["tournament"] = team.tournament
        context["matches"] = Match.objects.filter(Q(home_team=team) | Q(away_team=team)).select_related("stage", "group", "field", "home_team", "away_team").order_by("stage__sequence", "round_number", "sequence")
        return context
