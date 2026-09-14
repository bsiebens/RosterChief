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

from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View

from club.mixins import FeatureFlagMixin
from controlpanel.messages import notify
from members.views import ClubScopedPublicMixin

from .forms import TournamentRegistrationForm, TournamentRegistrationPlayerFormSet, players_from_formset
from .models import Tournament, TournamentRegistration, TournamentRegistrationPlayer
from .services.notifications import send_registration_confirmation_email

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
        contact_form, player_formset = self.get_forms()
        return self.render_page(request, tournament, contact_form, player_formset)

    def post(self, request, *args, **kwargs):
        tournament = self.get_tournament(request, kwargs["tournament_slug"])
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
    whoever submitted the registration may have no account at all. Read-only
    for V1: no cancel/edit from here, deliberately out of scope."""

    template_name = "tournaments/registration_status.html"

    def get(self, request, *args, **kwargs):
        registration = get_object_or_404(
            TournamentRegistration.objects.select_related("tournament"),
            tournament__club=request.club,
            tournament__slug=kwargs["tournament_slug"],
            status_token=kwargs["token"],
        )
        return render(request, self.template_name, {"tournament": registration.tournament, "registration": registration})
