"""Member-mode screens (M1-M7) plus the PWA plumbing (manifest, service worker,
icon, push subscribe) they all sit on top of. Coach mode (C1-C6) is a later
phase -- see the design handoff -- and has no
routes here yet.
"""

import datetime
import json
import uuid
from decimal import Decimal

from allauth.mfa.models import Authenticator
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.prefetch import GenericPrefetch
from django.core.paginator import Paginator
from django.db.models import F, Sum
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.utils.html import linebreaks
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.utils.safestring import mark_safe
from django.utils.translation import get_language, get_language_info, ngettext
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView
from waffle import flag_is_active

from authentication.models import User
from bugs.forms import BugReportForm
from bugs.models import BugReport
from bugs.services import file_report
from club.device import is_mobile_or_tablet
from club.models import ClubMembership, DuesInvoice
from club.services.access import current_season, get_current_season, has_management_access, teams_managed_by
from club.services.fees import open_dues_rows
from club.services.invoicing import DuesInvoicePDFError
from club.services.invoicing import invoice_pdf as dues_invoice_pdf
from club.services.onboarding import checklist_for
from club.services.sponsors import active_sponsors
from controlpanel.messages import notify
from events.models import Attendance, Event, EventAttachment, EventTask, OfficialSignup, RefereeSignup
from events.services.agenda import KIND_FILTERS as AGENDA_KIND_FILTERS
from events.services.agenda import member_agenda
from events.services.attachments import attachment_response, can_view_attachments
from events.services.calendar import agenda_groups
from events.services.game_push import follow_event, followed_team_ids, set_followed_teams, unfollow_event
from events.services.member_view import member_event_view
from events.services.officials import OfficialAssignmentError, accept_official_signup, decline_official_signup
from events.services.referees import RefereeAssignmentError, accept_referee_signup, decline_referee_signup
from events.services.rsvp import ANSWERABLE_STATUSES, RsvpError, submit_rsvp
from events.services.tasks import TaskClaimError, claim_task, unclaim_task
from formbuilder.models import FormSend, Submission
from formbuilder.services import FormSubmissionError, addressed_people, build_form, form_status_rows_for, is_send_open, submit_form
from members.models import FamilyMembership, Member
from members.services.family import managed_people
from members.views import ClubScopedPublicMixin
from news.models import News
from news.services import live_news, localized_news, member_news, render_body_excerpt, render_body_html
from notifications import preferences as notification_preferences
from notifications import quiet_hours
from notifications.models import NotificationCategory, NotificationPreference
from notifications.services import clear_all, inbox_for, mark_all_read, mark_read, unread_count
from registration.forms import MAX_REGISTRATION_ENTRIES
from registration.models import RegistrationBatch
from registration.services import (
    PricingError,
    RegistrationError,
    available_registration_products,
    priced_rows_with_jersey_fields,
    resolve_chosen_season,
    team_number_pools,
)
from registration.services.invoicing import RegistrationInvoicePDFError, batch_invoice_pdf, membership_ids_awaiting_confirmation
from registration.services.notifications import send_registration_confirmation_email
from rosterchief.client_ip import client_ip
from shop.models import Cart, CartItem, Order, ProductCategory, Voucher
from shop.services.cart import MAX_CART_QUANTITY, add_to_cart, cart_summary, catalog, open_cart, orders_for, set_quantity
from shop.services.checkout import CheckoutError, place_order
from shop.services.inventory import annotate_availability, annotate_cart_variants
from shop.services.invoices import ShopInvoicePDFError, render_invoice_pdf
from teams.models import StaffAssignment, Team, TeamMembership

from .club_switch import has_multiple_clubs
from .forms import _INPUT_CLASSES, _TEXTAREA_CLASSES, MemberProfileForm, style_dynamic_form
from .mixins import DesktopTemplateMixin, MemberAppMixin, PersonScopeMixin, ShopScopeMixin, shop_enabled
from .models import CalendarFeedToken, PushSubscription
from .services import event_ui, game_ui
from .services.calendar_feed import build_feed
from .services.home import home_summary
from .services.icons import ICON_SIZES, get_fallback_icon
from .services.payments import payment_cards, payments_overview
from .services.push import subscription_error
from .services.reregistration import EDITOR_ACTIONS, editor_form, entries_state, entry_cards, entry_formset, member_current_numbers, price_formset, state_to_data, submit_reregistration


class ManifestView(ClubScopedPublicMixin, View):
    """Per-club web-app manifest -- the club's own logo (or its server-rendered
    initials, see services/icons.py) becomes the home-screen icon, never a
    generic RosterChief mark."""

    def get(self, request):
        club = request.club
        icon = lambda size: reverse("mobile:icon", kwargs={"size": size})  # noqa: E731
        manifest = {
            "id": "/app/",
            "name": club.name,
            "short_name": club.name[:24],
            "lang": get_language() or "en",
            "start_url": "/app/",
            "scope": "/app/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#101e36",
            "theme_color": club.primary_color or "#101e36",
            "icons": [
                {"src": icon(192), "sizes": "192x192", "type": "image/png", "purpose": "any"},
                {"src": icon(512), "sizes": "512x512", "type": "image/png", "purpose": "any"},
                # Same artwork: the generated fallback keeps its initials inside the maskable safe zone; a club logo is used as uploaded.
                {"src": icon(192), "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
                {"src": icon(512), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
            "shortcuts": [
                {"name": str(_("Calendar")), "url": reverse("mobile:calendar"), "icons": [{"src": icon(192), "sizes": "192x192", "type": "image/png"}]},
                {"name": str(_("Notifications")), "url": reverse("mobile:notifications"), "icons": [{"src": icon(192), "sizes": "192x192", "type": "image/png"}]},
            ],
        }
        return JsonResponse(manifest, content_type="application/manifest+json")


class AppIconView(ClubScopedPublicMixin, View):
    def get(self, request, size):
        if size not in ICON_SIZES:
            raise Http404
        club = request.club
        if club.logo:
            return HttpResponseRedirect(club.logo.url)
        return HttpResponse(get_fallback_icon(club, size=size), content_type="image/png")


class OfflineView(View):
    """The precached page the service worker shows when a navigation fails offline.
    Public and club-agnostic (it carries no data), so it can be cached once."""

    def get(self, request):
        return HttpResponse(render_to_string("mobile/offline.html", {"club": getattr(request, "club", None)}, request=request))


class ServiceWorkerView(View):
    """Served literally at /app/sw.js (mobile/urls.py), not under /static/ --
    a service worker's default scope is everything below the path it's served
    from, so this is what makes the worker's scope /app/ without an explicit
    Service-Worker-Allowed header."""

    def get(self, request):
        return HttpResponse(render_to_string("mobile/sw.js", {}), content_type="application/javascript")


class PushSubscribeView(MemberAppMixin, View):
    """Called by mobile/static/mobile/app.js once the browser hands back a
    PushSubscription. Keyed on endpoint (globally unique per browser
    registration, see PushSubscription's own docstring): re-subscribing the
    same browser updates its row instead of creating a duplicate.

    Only ever touches the signed-in account's own rows: an endpoint already
    registered to someone else is neither reassigned nor deleted from here
    (409 / no-op), so knowing another browser's endpoint can't redirect or
    silence that person's notifications."""

    def post(self, request):
        member = Member.objects.filter(user=request.user).first()
        if member is None:
            return HttpResponseBadRequest("No member record for this account.")

        try:
            payload = json.loads(request.body)
            endpoint = payload["endpoint"]
            p256dh = payload["keys"]["p256dh"]
            auth = payload["keys"]["auth"]
        except KeyError, TypeError, ValueError:
            return HttpResponseBadRequest("Malformed subscription payload.")

        # The server later POSTs to this endpoint: only real browser push services,
        # with well-formed keys (see mobile.services.push.subscription_error).
        error = subscription_error(endpoint, p256dh, auth)
        if error:
            return HttpResponseBadRequest(error)

        if PushSubscription.objects.filter(endpoint=endpoint).exclude(member__user=request.user).exists():
            return JsonResponse({"status": "conflict"}, status=409)

        PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            member__user=request.user,
            defaults={
                "club": request.club,
                "member": member,
                "p256dh": p256dh,
                "auth": auth,
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:255],
            },
        )
        return JsonResponse({"status": "ok"})

    def delete(self, request):
        try:
            endpoint = json.loads(request.body)["endpoint"]
        except KeyError, TypeError, ValueError:
            return HttpResponseBadRequest("Malformed unsubscribe payload.")
        PushSubscription.objects.filter(endpoint=endpoint, member__user=request.user).delete()
        return JsonResponse({"status": "ok"})


class CalendarFeedView(ClubScopedPublicMixin, View):
    """The .ics subscription feed a calendar app polls -- URL-token
    authenticated, not LoginRequiredMixin (a calendar app can't do
    interactive login). See mobile.services.calendar_feed.build_feed for the
    feed's own shape/scope, and CalendarFeedToken's own docstring for why one
    token covers every club the account belongs to."""

    def get(self, request, token):
        feed_token = CalendarFeedToken.find(token)
        if feed_token is None:
            raise Http404("No such calendar feed.")
        me = Member.objects.filter(user=feed_token.user).first()
        feed = build_feed(request.club, managed_people(me, request.club))
        return HttpResponse(feed, content_type="text/calendar; charset=utf-8")


class CalendarFeedSettingsView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M5's "Calendar sync" row -- the webcal://.../https:// subscription
    links for this account's combined feed.

    Only a hash of the feed token is stored (see CalendarFeedToken), so the
    full URL can be shown exactly once: in the response to the POST that
    creates or replaces it. A plain visit only says whether a link exists;
    getting it again means replacing it, which immediately invalidates the
    old URL (also how a shared/leaked link is revoked)."""

    template_name = "mobile/calendar_feed_settings.html"
    screen_title = _("Calendar sync")
    active_tab = "me"

    def get_context_data(self, **kwargs):
        kwargs.setdefault("has_feed", CalendarFeedToken.objects.filter(user=self.request.user).exists())
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        replaced = CalendarFeedToken.objects.filter(user=request.user).exists()
        raw_token = CalendarFeedToken.issue(request.user)
        if replaced:
            title = _("Calendar link reset")
            body = _("Your old calendar link no longer works. Re-subscribe using the new one below.")
            notify(request, f"s|{title}|{body}")
        feed_url = request.build_absolute_uri(reverse("mobile:calendar_feed", kwargs={"token": raw_token}))
        webcal_url = feed_url.replace("https://", "webcal://", 1).replace("http://", "webcal://", 1)
        response = self.render_to_response(self.get_context_data(has_feed=True, feed_url=feed_url, webcal_url=webcal_url))
        # The one response that carries the raw token: keep it out of every cache.
        add_never_cache_headers(response)
        return response


class LiveGameUpdatesView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """The "Live game updates" row under Me -- which of the club's teams this
    account gets live game pushes for (goals, penalties, the result; see
    events.services.game_push). Opt-in, per account: one tick per team. Only
    there while the club has game timelines ("game_timeline" flag)."""

    template_name = "mobile/live_game_updates.html"
    screen_title = _("Live game updates")
    active_tab = "me"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not flag_is_active(request, "game_timeline"):
            raise Http404("Live game updates aren't available for this club.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        following = followed_team_ids(self.request.user, self.request.club)
        teams = Team.objects.filter(club=self.request.club).order_by("name")
        kwargs.setdefault("team_rows", [{"team": team, "following": team.pk in following} for team in teams])
        kwargs.setdefault("followed_games", game_ui.directly_followed_games(self.request.user, self.request.club))
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        # One followed game's "Stop following" (iOS: Following > Individual games > Unfollow).
        unfollow_id = request.POST.get("unfollow_game")
        if unfollow_id:
            event = Event.objects.filter(pk=unfollow_id, club=request.club, kind=Event.EventKind.GAME).first() if _is_uuid(unfollow_id) else None
            if event is not None:
                unfollow_event(request.user, request.club, event)
                notify(request, f"s|{_('Saved')}|{_('You no longer follow this game.')}")
            return redirect("mobile:live_game_updates")
        set_followed_teams(request.user, request.club, request.POST.getlist("team"))
        notify(request, f"s|{_('Saved')}|{_('Your live game updates have been updated.')}")
        return redirect("mobile:live_game_updates")


class NotificationSettingsView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """The "Notifications" row under Me (iOS: Notifications): per-category push/email
    switches for this club with an "Applies to" picker (Everyone / each managed child),
    quiet hours, and -- last -- whether notifications are also emailed to this account
    (authentication.User.email_notifications), account-wide like the app API's
    PATCH /me/settings."""

    template_name = "mobile/notification_settings.html"
    screen_title = _("Notifications")
    active_tab = "me"

    COMMON_ZONES = (
        "Europe/Brussels",
        "Europe/Amsterdam",
        "Europe/London",
        "Europe/Paris",
        "Europe/Berlin",
        "Europe/Madrid",
        "Europe/Rome",
        "Europe/Lisbon",
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Los_Angeles",
        "America/Toronto",
        "Australia/Sydney",
        "UTC",
    )

    def _notification_person(self, request):
        """The child whose switches are shown/saved (``?person=<id>`` / the form's hidden ``person``); ``None`` =
        the account holder, whose switches are the club's."""
        wanted = request.POST.get("person") or request.GET.get("person")
        for person in self.managed_people[1:]:
            if str(person.pk) == wanted:
                return person
        return None

    def _redirect_url(self, person):
        url = reverse("mobile:notification_settings")
        return f"{url}?person={person.pk}" if person else url

    def get_context_data(self, **kwargs):
        club = self.request.club
        person = self._notification_person(self.request)
        effective = notification_preferences.matrix(self.request.user, club, person)
        overridden = notification_preferences.overrides(self.request.user, club, person)
        kwargs.setdefault("choices", User.EmailNotifications.choices)
        kwargs.setdefault("current", self.request.user.email_notifications)
        kwargs["category_rows"] = [{"key": key, "label": label, "push": effective[key]["push"], "email": effective[key]["email"], "overridden": key in overridden} for key, label in NotificationCategory.choices]
        kwargs["master"] = notification_preferences.master_matrix(self.request.user, club, person)
        kwargs["customised"] = bool(overridden) or bool(notification_preferences.master_overrides(self.request.user, club, person))
        kwargs["selected_person"] = person
        kwargs["children"] = self.managed_people[1:]
        hours = quiet_hours.get(self.request.user)
        zone = hours.timezone if hours else settings.TIME_ZONE
        zones = list(dict.fromkeys([zone, settings.TIME_ZONE, *self.COMMON_ZONES]))
        kwargs["quiet"] = {"enabled": bool(hours and hours.enabled), "start": f"{hours.start:%H:%M}" if hours else "22:00", "end": f"{hours.end:%H:%M}" if hours else "07:00", "timezone": zone}
        kwargs["zones"] = zones
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        if "category_switches" in request.POST:
            return self._save_categories(request)
        if "reset_category" in request.POST:
            return self._reset_category(request)
        if "reset_all" in request.POST:
            return self._reset_all(request)
        if "quiet_hours" in request.POST:
            return self._save_quiet_hours(request)
        value = request.POST.get("email_notifications")
        if value not in User.EmailNotifications.values:
            return HttpResponseBadRequest("Unknown choice.")
        request.user.email_notifications = value
        request.user.save(update_fields=["email_notifications"])
        notify(request, f"s|{_('Saved')}|{_('Your notification email setting has been updated.')}")
        return redirect("mobile:notification_settings")

    def _save_categories(self, request):
        """Writes rows only where a switch differs from what already applies here (for the chosen person)."""
        person = self._notification_person(request)
        effective = notification_preferences.matrix(request.user, request.club, person)
        masters = notification_preferences.master_matrix(request.user, request.club, person)
        for channel in NotificationPreference.Channel.values:
            if "master_switches" not in request.POST:  # older forms never send the masters: leave them alone
                break
            wanted_master = f"master__{channel}" in request.POST
            if wanted_master != masters[channel]:
                notification_preferences.set_master(request.user, request.club, channel, wanted_master, member=person)
                masters[channel] = wanted_master
        for category in NotificationCategory.values:
            for channel in NotificationPreference.Channel.values:
                if not masters[channel]:  # the category boxes of a channel switched off as a whole are disabled: nothing to read from them
                    continue
                wanted = f"{channel}__{category}" in request.POST
                if wanted != effective[category][channel]:
                    notification_preferences.set_preference(request.user, request.club, category, channel, wanted, member=person)
        if person:
            notify(request, f"s|{_('Saved')}|{_('Your notification switches for this person have been updated.')}")
        else:
            notify(request, f"s|{_('Saved')}|{_('Your notification switches for this club have been updated.')}")
        return redirect(self._redirect_url(person))

    def _reset_category(self, request):
        category = request.POST["reset_category"]
        if category not in NotificationCategory.values:
            return HttpResponseBadRequest("Unknown category.")
        person = self._notification_person(request)
        notification_preferences.reset(request.user, request.club, category, member=person)
        notify(request, f"s|{_('Saved')}|{_('This category follows your default again.')}")
        return redirect(self._redirect_url(person))

    def _reset_all(self, request):
        person = self._notification_person(request)
        notification_preferences.reset(request.user, request.club, None, member=person)
        notify(request, f"s|{_('Saved')}|{_('All switches follow your default again.')}")
        return redirect(self._redirect_url(person))

    def _save_quiet_hours(self, request):
        try:
            start = datetime.datetime.strptime(request.POST.get("start", ""), "%H:%M").time()
            end = datetime.datetime.strptime(request.POST.get("end", ""), "%H:%M").time()
            quiet_hours.set_quiet_hours(request.user, enabled="enabled" in request.POST, start=start, end=end, tz=request.POST.get("timezone", ""))
        except ValueError:
            return HttpResponseBadRequest("Invalid quiet hours.")
        notify(request, f"s|{_('Saved')}|{_('Your quiet hours have been updated.')}")
        return redirect(self._redirect_url(self._notification_person(request)))


# ---------------------------------------------------------------------------
# Home / Calendar / Event sheet helpers (iOS parity) -- the presentation lives in
# mobile/services/event_ui.py, the rules in events/services/*.
# ---------------------------------------------------------------------------


def _return_url(request, default):
    """Where an inline answer (a Home card, a Calendar row) goes back to: the page it was posted
    from (the ``return`` field), as long as that is a path on this site."""
    target = request.POST.get("return", "")
    if target.startswith("/") and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return default


def _home_cards(view, summary):
    """Home's "next up" cards: one per event, everyone whose next event it is sharing it (see
    mobile.services.home). A still-unanswered invitation whose reply deadline has passed gets a
    read-only "closed" card first, as the single hero used to."""
    source = list(summary["member_cards"])
    hero = summary["hero_attendance"]
    if hero is not None and summary["rsvp_closed"]:
        source.insert(0, {"kind": "rsvp_closed", "event": hero.event, "attendances": [hero]})
    mine = event_ui.household_tasks(view.me, view.request.club, [card["event"].pk for card in source])
    cards = []
    for card in source:
        event = card["event"]
        lines = [{"attendance": attendance, "name": attendance.member.first_name, "drop_status": "dropout" if attendance.status == Attendance.AttendanceStatus.SELECTED else "absent"} for attendance in card["attendances"]]
        cards.append({"kind": card["kind"], "event": event, "style": event_ui.kind_style(event.kind), "lines": lines, "closed": card["kind"] == "rsvp_closed", "my_tasks": mine.get(event.pk, [])})
    return cards


def _event_context(view, event, *, in_sheet=False, error=None) -> dict:
    """Everything the event page and the event sheet render (mobile/_event_body.html)."""
    request = view.request
    officials = flag_is_active(request, "officials")
    # Line-up, sign-ups, tasks, per-person answers, blocked people and the squad
    # summary come from events.services.member_view, shared with the app API.
    member_view = member_event_view(event, request.club, view.managed_people, view.me, officials_enabled=officials)
    return {
        "event": event,
        "rsvp_closed": member_view["rsvp_closed"],
        "started": member_view["started"],
        "lineup": member_view["lineup"],
        "lineup_categories": member_view["lineup_categories"],
        "referee_signups": member_view["referee_signups"],
        "official_signups": member_view["official_signups"],
        "tasks": member_view["tasks"],
        "attachments": member_view["attachments"],
        "your_answers": event_ui.shape_answers(member_view, event),
        "blocked_signups": member_view["blocked_signups"],
        "squad_summary": member_view["squad_summary"],
        "hero": event_ui.hero_for(event, rsvp_rows_present=bool(member_view["your_answers"]), referee_signups=member_view["referee_signups"], official_signups=member_view["official_signups"]),
        "maps_url": event_ui.maps_url(event.location, request.META.get("HTTP_USER_AGENT", "")),
        "in_sheet": in_sheet,
        "sheet_error": error,
        **_game_context(request, event),
    }


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def _game_context(request, event) -> dict:
    """The follow toggle and the timeline card of a game (club has the ``game_timeline`` flag)."""
    if event.kind != Event.EventKind.GAME:
        return {}
    if not flag_is_active(request, "game_timeline"):
        return {}
    context = {"game_tl": game_ui.timeline_context(event)}
    if game_ui.is_followable(event):
        context["game_follow"] = game_ui.follow_context(request.user, request.club, event)
    return context


def render_event_sheet(view, event, error=None):
    """The event sheet's content (the dialog fetches it and every answer inside it swaps it
    again). ``error`` shows a refusal -- a closed deadline, a full task -- inside the sheet."""
    response = render(view.request, "mobile/_event_sheet.html", _event_context(view, event, in_sheet=True, error=error))
    add_never_cache_headers(response)
    return response


class HomeView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M1 -- the design handoff's M1 section: two
    independent "next up" hero cards for everyone currently in scope -- the
    soonest event nobody's answered yet (with a quick In/Out RSVP -- see
    EventDetailView.post below), shown only when such an event exists, and
    the soonest event someone's actually going to (PRESENT/SELECTED), shown
    as a read-only overview since there's nothing left to ask -- then a
    "needs your answer" list of upcoming events still NO_RESPONSE/MAYBE
    (excluding whichever one is already the top hero card) -- merged with
    any still-INVITED RefereeSignup/OfficialSignup for the same people,
    since those are exactly the same kind of "we need a yes/no from you"
    ask, just on a different model -- an
    "open tasks" list of unclaimed EventTask slots on those same in-scope
    upcoming events (regardless of RSVP status -- a task is open to anyone,
    not tied to one person's reply), a season-dues card per person who owes
    money, and a news teaser. Every card is independently optional -- an
    empty-state screen is just those `if`s all being falsy.

    Scoped to ``self.people_in_scope`` (mobile/mixins.py), not just
    ``scope_person`` -- with the header's "All" chip now the default the
    moment there's more than one managed person, this screen aggregates
    across everyone in that case rather than showing only one person's
    cards.
    """

    template_name = "mobile/home.html"
    screen_title = _("Home")
    active_tab = "home"

    def _followed_context(self):
        """Followed games (iOS FollowedGameCard): live ones above the cards, the rest below."""
        games = game_ui.home_followed_games(self.request.user, self.request.club, flag_is_active(self.request, "game_timeline"))
        return {"followed_live": [game for game in games if game["status"] == "live"], "followed_rest": [game for game in games if game["status"] != "live"]}

    def get_context_data(self, **kwargs):
        # The cards themselves (and their caps) are mobile.services.home, shared with the
        # app API's GET /home.
        summary = home_summary(self.request.club, self.people_in_scope, officials_enabled=flag_is_active(self.request, "officials"))
        member_cards = _home_cards(self, summary)
        # A person's soonest unanswered invitation is a card above, so the "Needs your answer" list
        # leaves it out (the iOS Home does the same) -- the context's own ``needs_answer`` stays whole.
        card_attendance_ids = {attendance.pk for card in summary["member_cards"] for attendance in card["attendances"]}
        needs_answer_view = [row for row in summary["needs_answer"] if "attendance" not in row or row["attendance"].pk not in card_attendance_ids]
        needs_answer_view_total = summary["needs_answer_total"] - (len(summary["needs_answer"]) - len(needs_answer_view))
        return super().get_context_data(
            member_cards=member_cards,
            needs_answer_view=needs_answer_view,
            needs_answer_view_total=needs_answer_view_total,
            needs_answer_view_remaining=needs_answer_view_total - len(needs_answer_view),
            hero_attendance=summary["hero_attendance"],
            rsvp_closed=summary["rsvp_closed"],
            going_attendance=summary["going_attendance"],
            needs_answer=summary["needs_answer"],
            needs_answer_remaining=summary["needs_answer_total"] - len(summary["needs_answer"]),
            open_tasks=summary["open_tasks"],
            open_tasks_remaining=summary["open_tasks_total"] - len(summary["open_tasks"]),
            dues_rows=summary["dues_rows"],
            news_items=summary["news_items"],
            forms_to_complete=summary["forms_to_complete"],
            # Club-wide, not person-specific -- shown regardless of managed_people,
            # unlike every other card on this screen. Reshuffled on every request
            # (see club.services.sponsors.active_sponsors) rather than once per
            # session, same as the public-website sponsor strip it shares logic with.
            sponsors=active_sponsors(self.request.club, randomize=True),
            **self._followed_context(),
            **kwargs,
        )


class CalendarView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M3 -- README's M3 section: a chronological agenda list (not the desktop
    week/month grid events.services.calendar was built for) grouped under
    "This week"/"Next week", then everything further out grouped by calendar
    month via events.services.calendar.agenda_groups (also behind management.
    views.EventListView's own "List" mode and mobile.coach_views.
    CoachScheduleView, so all three stay behaviourally identical) -- an
    unbounded agenda rather than a fixed lookahead window, since a hard
    cutoff just hid events a member would reasonably expect to still find
    here. No ?month= paging beyond that grouping -- there's no season
    browser here, just "everything upcoming, readably grouped".

    Always scoped to every one of ``self.managed_people`` -- unlike Home,
    this screen has no person switcher and no "every club event" toggle: it's
    just "what is my family invited to", full stop. A ``?kind=`` filter (All/
    Games/Practices, the two dominant event kinds) narrows that down -- a
    real, working version of the design mock's own "Games only" pill, not
    reproducing its List/Month toggle (that needs a whole second view mode,
    not a filter) or its per-person "All members" (removed on purpose, see
    above).
    """

    template_name = "mobile/calendar.html"
    screen_title = _("Calendar")
    active_tab = "calendar"

    #: ?kind= values this screen actually understands, mapped to the Event.EventKind
    #: they filter on -- anything else (including no param at all) means "All".
    KIND_FILTERS = AGENDA_KIND_FILTERS

    #: Pill styling for a per-person RSVP status (assets/mobile.css's .pill-*).
    STATUS_PILL_CLASSES = {
        Attendance.AttendanceStatus.PRESENT: "pill-ok",
        Attendance.AttendanceStatus.SELECTED: "pill-ok",
        Attendance.AttendanceStatus.ABSENT: "pill-danger",
        Attendance.AttendanceStatus.NOT_SELECTED: "pill-neutral",
        Attendance.AttendanceStatus.EXCUSED: "pill-neutral",
        Attendance.AttendanceStatus.MAYBE: "pill-warn",
        Attendance.AttendanceStatus.NO_RESPONSE: "pill-warn",
    }

    def _calendar_url(self, **overrides):
        """This screen's URL with the current query string, ``overrides`` applied (``None`` drops a
        parameter) -- every link in the header, the filter form and the month grid goes through it."""
        params = self.request.GET.copy()
        for key, value in overrides.items():
            if value is None:
                params.pop(key, None)
            else:
                params[key] = value
        query = params.urlencode()
        return reverse("mobile:calendar") + (f"?{query}" if query else "")

    def get_context_data(self, **kwargs):
        request = self.request
        now = timezone.now()
        today = timezone.localdate()
        zone = timezone.get_current_timezone()
        midnight = timezone.make_aware(datetime.datetime.combine(today, datetime.time.min), zone)
        kind_filter = request.GET.get("kind")
        if kind_filter not in self.KIND_FILTERS:
            kind_filter = None
        # Desktop shows the month grid and the agenda side by side (iPad-style), so ?view= only matters on the phone.
        split = not is_mobile_or_tablet(request)
        month_mode = request.GET.get("view") == "month" and not split
        show_earlier = not month_mode and request.GET.get("earlier") == "1"
        season = get_current_season(request)

        # Like the iOS Calendar, a person-scope menu narrows it to one person (``?as=``); without it,
        # it is everyone the account acts for. Only worth naming whose row it is once there's more
        # than one person in scope to tell apart.
        people = self.people_in_scope
        show_member = len(people) > 1

        if month_mode:
            month_start = event_ui.parse_month(request.GET.get("month"), today)
            window_start, window_end = event_ui.month_window(month_start)
        else:
            month_start = None
            window_end = None
            window_start = midnight
            if show_earlier:
                earliest = min(season.start_date, today) if season else today - datetime.timedelta(days=90)
                window_start = timezone.make_aware(datetime.datetime.combine(earliest, datetime.time.min), zone)
            if split:
                # The agenda (open-ended) plus whatever part of the shown month lies before it.
                month_start = event_ui.parse_month(request.GET.get("month"), today)
                window_start = min(window_start, event_ui.month_window(month_start)[0])

        # The merged agenda itself (invitations, referee/official sign-ups, events blocked
        # by an open onboarding requirement) is events.services.agenda, shared with the app
        # API; this only shapes each row for the template. Declined sign-ups are dropped,
        # accepted ones stay visible as a confirmed commitment; the officials rows follow
        # the "officials" flag like EventDetailView's own card. The list starts at the beginning of
        # today (so a game played this morning still shows its score); ``?earlier=1`` reaches back
        # to the season's start.
        agenda = member_agenda(request.club, people, kind=kind_filter, officials_enabled=flag_is_active(request, "officials"), start=window_start, end=window_end)
        rows = []
        for row in agenda:
            person = row["member"]
            member = person if show_member else None
            shaped = {"event": row["event"], "type": row["type"], "person": person}
            if row["type"] == "rsvp":
                attendance = row["attendance"]
                shaped.update(attendance=attendance, pill_class=self.STATUS_PILL_CLASSES.get(attendance.status, "pill-neutral"), pill_label=attendance.get_status_display(), member=member)
            elif row["type"] == "referee":
                shaped.update(signup=row["signup"], referee_signup=row["signup"], referee_member=member)
            elif row["type"] == "official":
                shaped.update(signup=row["signup"], official_signup=row["signup"], official_member=member)
            else:
                shaped.update(requirements=row["requirements"], blocked_requirements=row["requirements"], blocked_member=member)
            rows.append(shaped)

        # The filter menu (iOS: "Show on calendar" + "Requests"). Opt-in through the URL: once the form
        # was submitted (``f=1``) only the ticked kinds/requests show.
        filtered = request.GET.get("f") == "1"
        selected_kinds = set(request.GET.getlist("k")) if filtered else None
        has_referee_rows = any(row["type"] == "referee" for row in rows)
        has_official_rows = any(row["type"] == "official" for row in rows)
        show_referee = "referee" in request.GET.getlist("rq") if filtered else True
        show_official = "official" in request.GET.getlist("rq") if filtered else True
        present_kinds = {row["event"].kind for row in rows}
        menu_kinds = [
            {"value": kind, "label": event_ui.kind_style(kind)["filter_label"], "checked": selected_kinds is None or kind in selected_kinds}
            for kind in event_ui.KIND_ORDER
            if kind != Event.EventKind.OTHER or kind in present_kinds or (selected_kinds is not None and kind not in selected_kinds)
        ]
        menu_values = {item["value"] for item in menu_kinds}
        hidden_kinds = [kind for kind in event_ui.KIND_ORDER if kind not in menu_values and (selected_kinds is None or kind in selected_kinds)]
        filter_active = filtered and (any(not item["checked"] for item in menu_kinds) or (has_referee_rows and not show_referee) or (has_official_rows and not show_official))
        rows = event_ui.apply_filters(rows, kinds=selected_kinds, referee=show_referee, official=show_official)

        groups = event_ui.build_groups(rows, me=self.me, now=now, show_names=show_member)

        this_week = next_week = later_months = earlier_months = []
        month_pane = None
        split_day = None
        if split:
            split_day = event_ui.parse_day(request.GET.get("day"))
            if split_day is not None and not (split_day.replace(day=1) == month_start or abs((split_day - month_start).days) < 40):
                split_day = None
            # No day picked: nothing is filled in the grid (today stays ringed) and the list is the agenda.
            month_pane = event_ui.month_pane(groups, month_start=month_start, selected=split_day or datetime.date.min, today=today, url_for=self._calendar_url)
            for week in month_pane["weeks"]:
                for cell in week:
                    if cell["selected"]:
                        cell["url"] = self._calendar_url(month=month_start.strftime("%Y-%m"), day=None)
            past = [group for group in groups if group["event"].start < midnight]
            upcoming = [group for group in groups if group["event"].start >= midnight]
            this_week, next_week, later_months = agenda_groups(upcoming, start_of=lambda group: group["event"].start, today=today)
            earlier_months = event_ui.months_of(past)
            event_ui.mark_today_anchor(this_week, next_week, *[month["items"] for month in later_months], today=today)
            if split_day is not None:
                month_pane["selected_groups"] = [group for group in groups if timezone.localtime(group["event"].start).date() == split_day]
        elif month_mode:
            selected = event_ui.parse_day(request.GET.get("day"))
            if selected is None or selected.replace(day=1) != month_start:
                selected = today if today.replace(day=1) == month_start else month_start
            month_pane = event_ui.month_pane(groups, month_start=month_start, selected=selected, today=today, url_for=self._calendar_url)
        else:
            past = [group for group in groups if group["event"].start < midnight]
            upcoming = [group for group in groups if group["event"].start >= midnight]
            this_week, next_week, later_months = agenda_groups(upcoming, start_of=lambda group: group["event"].start, today=today)
            earlier_months = event_ui.months_of(past)
            event_ui.mark_today_anchor(this_week, next_week, *[month["items"] for month in later_months], today=today)

        scope_options = [{"label": _("All members"), "url": self._calendar_url(**{"as": "all"}), "active": self.scope_everyone}]
        for person in self.managed_people:
            scope_options.append({"label": _("Me") if person == self.me else person.get_full_name(), "url": self._calendar_url(**{"as": str(person.pk)}), "active": not self.scope_everyone and person == self.scope_person})
        if self.scope_everyone or self.scope_person is None:
            scope_label = _("All members")
        else:
            scope_label = _("Me") if self.scope_person == self.me else self.scope_person.first_name

        return super().get_context_data(
            this_week=this_week,
            next_week=next_week,
            later_months=later_months,
            earlier_months=earlier_months,
            has_groups=bool(groups),
            kind_filter=kind_filter or "",
            view_mode="month" if month_mode else "list",
            split=split,
            split_day=split_day,
            month_pane=month_pane,
            list_url=self._calendar_url(view=None, month=None, day=None, earlier=None),
            month_url=self._calendar_url(view="month", month=None, day=None, earlier=None),
            agenda_url=self._calendar_url(day=None),
            earlier_url=self._calendar_url(earlier="1", today="1") if not month_mode and not show_earlier and season is not None and season.start_date < today else None,
            show_earlier=show_earlier,
            scroll_today=not month_mode and request.GET.get("today") == "1",
            # Where an inline answer comes back to: this very screen, without the one-shot "jump to today".
            return_url=self._calendar_url(today=None),
            scope_options=scope_options,
            scope_label=scope_label,
            show_scope_menu=len(self.managed_people) > 1,
            menu_kinds=menu_kinds,
            hidden_kinds=hidden_kinds,
            has_referee_rows=has_referee_rows,
            has_official_rows=has_official_rows,
            show_referee=show_referee,
            show_official=show_official,
            filter_active=filter_active,
            filter_hidden=[(key, request.GET[key]) for key in ("as", "view", "month", "day") if key in request.GET],
            **kwargs,
        )


class RefereeSignupRespondView(PersonScopeMixin, LoginRequiredMixin, View):
    """Accept/decline a referee invite, for self.me or any managed person
    (a referee-eligible child is exactly as real as a referee-eligible
    parent) -- from a Calendar row (mobile/_calendar_referee_row.html) or
    the same event's own detail page (event_detail.html's own "Refereeing"
    card, for the same signup). Routes through events.services.referees.
    accept_referee_signup/decline_referee_signup. Accepting only records
    interest: the club confirms who actually goes from the referee-management
    screen, so no capacity is involved here.
    Boosted (no explicit hx-boost="false") -- unlike event_detail's own
    RSVP forms, nothing here is Alpine-owned/toggled, so there's no
    htmx/Alpine conflict to dodge."""

    def post(self, request, *args, **kwargs):
        signup = get_object_or_404(RefereeSignup, pk=kwargs["signup_id"], member__in=self.managed_people, event__club=request.club)
        response = request.POST.get("response")
        # From the event sheet the answer stays in the sheet: it is re-rendered, a refusal shown inside it.
        sheet = request.POST.get("surface") == "sheet"
        sheet_error = None

        if response == "accept":
            try:
                accept_referee_signup(signup)
                body = _("%(name)s is signed up to referee -- the club will confirm who goes.") % {"name": signup.member.get_full_name()}
                if not sheet:
                    notify(request, f"s|{_('Signed up to referee')}|{body}")
            except RefereeAssignmentError as exc:
                sheet_error = str(exc)
                if not sheet:
                    notify(request, f"e|{_('Could not sign up')}|{exc}")
        elif response == "decline":
            decline_referee_signup(signup)
            body = _("%(name)s won't be refereeing this one -- thanks for letting us know.") % {"name": signup.member.get_full_name()}
            if not sheet:
                notify(request, f"s|{_('Declined')}|{body}")
        else:
            return HttpResponseBadRequest(_("Unknown response."))

        if sheet:
            return render_event_sheet(self, signup.event, error=sheet_error)
        if request.POST.get("next") == "event_detail":
            return HttpResponseRedirect(reverse("mobile:event_detail", kwargs={"pk": signup.event_id}))
        return HttpResponseRedirect(_return_url(request, reverse("mobile:calendar")))


class OfficialSignupRespondView(PersonScopeMixin, LoginRequiredMixin, View):
    """The officials counterpart to RefereeSignupRespondView -- same shape,
    same reasoning (see that view's own docstring)."""

    def post(self, request, *args, **kwargs):
        signup = get_object_or_404(OfficialSignup, pk=kwargs["signup_id"], member__in=self.managed_people, event__club=request.club)
        response = request.POST.get("response")
        sheet = request.POST.get("surface") == "sheet"
        sheet_error = None

        if response == "accept":
            try:
                accept_official_signup(signup)
                body = _("%(name)s is signed up as an official -- the club will confirm who goes.") % {"name": signup.member.get_full_name()}
                if not sheet:
                    notify(request, f"s|{_('Signed up as official')}|{body}")
            except OfficialAssignmentError as exc:
                sheet_error = str(exc)
                if not sheet:
                    notify(request, f"e|{_('Could not sign up')}|{exc}")
        elif response == "decline":
            decline_official_signup(signup)
            body = _("%(name)s won't be an official for this one -- thanks for letting us know.") % {"name": signup.member.get_full_name()}
            if not sheet:
                notify(request, f"s|{_('Declined')}|{body}")
        else:
            return HttpResponseBadRequest(_("Unknown response."))

        if sheet:
            return render_event_sheet(self, signup.event, error=sheet_error)
        if request.POST.get("next") == "event_detail":
            return HttpResponseRedirect(reverse("mobile:event_detail", kwargs={"pk": signup.event_id}))
        return HttpResponseRedirect(_return_url(request, reverse("mobile:calendar")))


class EventAttachmentDownloadView(PersonScopeMixin, LoginRequiredMixin, View):
    """Opens one of an event's attachments -- the managers, the team's staff, and whoever answers
    for an invited person (events.services.attachments.can_view_attachments). Anyone else gets a
    404 that doesn't confirm the file exists."""

    def get(self, request, pk, attachment_pk):
        attachment = get_object_or_404(EventAttachment.objects.select_related("event"), pk=attachment_pk, event_id=pk, event__club=request.club)
        if not can_view_attachments(request.user, request.club, attachment.event, self.managed_people):
            raise Http404("No such file.")
        return attachment_response(attachment)


class EventTaskRespondView(PersonScopeMixin, LoginRequiredMixin, View):
    """Claim/unclaim one slot of an event task (management._event_task_panel.
    html's own read-only counterpart) -- always as self.me, not a managed-
    person picker like RSVP has: a logistics task ("bring fruit") belongs to
    whichever parent/member is handling it, not a specific child on the
    roster. Routes through events.services.tasks.claim_task/unclaim_task, so
    capacity is enforced in the one place the panel's own read-only claim
    list already reflects."""

    def post(self, request, *args, **kwargs):
        if self.me is None:
            return HttpResponseBadRequest(_("No member record for this account."))

        task = get_object_or_404(EventTask, pk=kwargs["task_id"], event__club=request.club)
        response = request.POST.get("response")
        # From the event sheet (or an inline Claim on Home) the screen is answered in place.
        sheet = request.POST.get("surface") == "sheet"
        sheet_error = None

        if response == "claim":
            try:
                claim_task(task, self.me)
                if not sheet:
                    notify(request, f"s|{_('Claimed')}|" + _("You're down for “%(title)s”.") % {"title": task.title})
            except TaskClaimError as exc:
                sheet_error = str(exc)
                if not sheet:
                    notify(request, f"e|{_('Could not claim')}|{exc}")
        elif response == "unclaim":
            claim = task.claims.filter(member=self.me).first()
            if claim is not None:
                unclaim_task(claim)
                if not sheet:
                    notify(request, f"w|{_('Removed')}|" + _("You're no longer down for “%(title)s”.") % {"title": task.title})
        else:
            return HttpResponseBadRequest(_("Unknown response."))

        if sheet:
            return render_event_sheet(self, task.event, error=sheet_error)
        if request.POST.get("next") == "event_detail":
            return HttpResponseRedirect(reverse("mobile:event_detail", kwargs={"pk": task.event_id}))
        return HttpResponseRedirect(_return_url(request, reverse("mobile:calendar")))


class EventDetailView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M2 -- the design handoff's M2 section:
    "answer for several". A hero header (no event photos in this codebase --
    a solid dark block instead, like M1's hero card), an event-detail card
    (Face-off/Meet/Where -- the design mock's extra "Kit" row and dressing-room
    detail have no backing Event field, so they're simply not shown), a
    per-managed-person RSVP card scoped to ``self.managed_people`` who
    actually have an ``Attendance`` row for this event (i.e. are invited --
    not every managed person necessarily is), and a club/team-visible
    squad-response aggregate (counts only, never who-answered-what) shown
    only when the event has an actual team roster to aggregate.

    POST is still M1's quick In/Out RSVP action (now also accepting "maybe"
    for M2's three-way buttons), reused by any screen that posts the same
    {status, member_id} shape at this URL. An optional ``next=event_detail``
    field redirects back here instead of Home -- M2's own forms send it so a
    parent answering for several people in a row sees each update land
    without bouncing away; M1's/M3's existing forms don't send it, so they
    keep redirecting to Home unchanged.
    """

    template_name = "mobile/event_detail.html"
    screen_title = _("Event")
    active_tab = "calendar"

    def _wants_sheet(self) -> bool:
        return self.request.GET.get("sheet") == "1"

    def get_template_names(self):
        # The bottom sheet (mobile/static/js/mobile-sheet.js fetches ``?sheet=1``) is a fragment; a
        # direct visit (a push notification, a bookmark) still gets the full page below.
        if self._wants_sheet():
            return ["mobile/_event_sheet.html"]
        return super().get_template_names()

    def get_context_data(self, **kwargs):
        event = get_object_or_404(Event.objects.select_related("location", "opponent"), pk=self.kwargs["pk"], club=self.request.club)
        return super().get_context_data(screen_title=event.title, **_event_context(self, event, in_sheet=self._wants_sheet()), **kwargs)

    def post(self, request, *args, **kwargs):
        # The rules themselves (dropout, deadline, reasons) live in events.services.rsvp,
        # shared with the app API; this only resolves who and what, and maps refusals.
        status = request.POST.get("status")
        if status not in ANSWERABLE_STATUSES:
            return HttpResponseBadRequest(_("Unknown RSVP status."))

        # Every current caller (Home's hero, M2's per-person rows) always sends an
        # explicit member_id -- this is just a defensive fallback for one that doesn't.
        fallback_member = self.scope_person or self.me
        member_id = request.POST.get("member_id") or (str(fallback_member.pk) if fallback_member else None)
        member = next((person for person in self.managed_people if str(person.pk) == member_id), None) if member_id else None
        if member is None:
            return HttpResponseBadRequest(_("You can't RSVP for that person."))

        event = get_object_or_404(Event.objects.select_related("location", "opponent"), pk=kwargs["pk"], club=request.club)
        # Only an invited person can answer: an RSVP updates their Attendance row and
        # never creates one for an event the person wasn't invited to.
        existing = get_object_or_404(Attendance.objects.select_related("event", "member"), event=event, member=member)

        # Where the answer was given decides what comes back: the sheet re-renders itself (a refusal
        # shown inside it), an inline Home/Calendar answer goes back to that page, everything else
        # keeps the original redirects.
        sheet = request.POST.get("surface") == "sheet"
        inline = bool(request.POST.get("return"))
        try:
            submit_rsvp(existing, status, request.POST.get("note", ""))
        except RsvpError as exc:
            if sheet:
                return render_event_sheet(self, event, error=str(exc.message))
            if inline:
                notify(request, f"e|{_('Could not send your answer')}|{exc.message}")
                return HttpResponseRedirect(_return_url(request, reverse("mobile:home")))
            if exc.code == "event_started":
                notify(request, f"e|{_('Too late to reply')}|{exc.message}")
                return HttpResponseRedirect(reverse("mobile:event_detail", kwargs={"pk": event.pk}))
            return HttpResponseBadRequest(exc.message)

        if sheet:
            return render_event_sheet(self, event)
        if request.POST.get("next") == "event_detail":
            return HttpResponseRedirect(reverse("mobile:event_detail", kwargs={"pk": event.pk}))
        return HttpResponseRedirect(_return_url(request, reverse("mobile:home")))


class GameFollowView(PersonScopeMixin, LoginRequiredMixin, View):
    """Follow / stop following one game (the app API's PUT / DELETE /events/{id}/follow, through
    the same events.services.game_push). Answers with the toggle fragment for htmx; a game followed
    through a followed team stays followed (unfollow the team on Me > Live game updates)."""

    def post(self, request, *args, **kwargs):
        if not flag_is_active(request, "game_timeline"):
            raise Http404("Game timelines aren't available for this club.")
        event = get_object_or_404(Event.objects.prefetch_related("teams"), pk=kwargs["pk"], club=request.club)
        if event.kind != Event.EventKind.GAME:
            return HttpResponseBadRequest(_("Only games can be followed."))
        if request.POST.get("follow") == "1":
            follow_event(request.user, request.club, event)
        else:
            unfollow_event(request.user, request.club, event)
        if not request.headers.get("HX-Request"):
            return HttpResponseRedirect(reverse("mobile:event_detail", kwargs={"pk": event.pk}))
        response = render(request, "mobile/_game_follow.html", {"follow": game_ui.follow_context(request.user, request.club, event), "in_sheet": request.POST.get("surface") == "sheet"})
        add_never_cache_headers(response)
        return response


class GameTimelineView(PersonScopeMixin, LoginRequiredMixin, View):
    """The game timeline card as a fragment -- what a live game's card re-fetches every 15 s."""

    def get(self, request, *args, **kwargs):
        if not flag_is_active(request, "game_timeline"):
            raise Http404("Game timelines aren't available for this club.")
        event = get_object_or_404(Event.objects.select_related("location", "opponent").prefetch_related("teams"), pk=kwargs["pk"], club=request.club, kind=Event.EventKind.GAME)
        response = render(request, "mobile/_game_timeline.html", {"tl": game_ui.timeline_context(event)})
        add_never_cache_headers(response)
        return response


class FollowedGamesView(PersonScopeMixin, LoginRequiredMixin, View):
    """Home's followed-game cards as out-of-band fragments (live slot + the rest), polled while any is live."""

    def get(self, request, *args, **kwargs):
        if not flag_is_active(request, "game_timeline"):
            raise Http404("Game timelines aren't available for this club.")
        games = game_ui.home_followed_games(request.user, request.club, True)
        context = {"followed_live": [game for game in games if game["status"] == "live"], "followed_rest": [game for game in games if game["status"] != "live"], "slot": "both", "oob": True}
        response = render(request, "mobile/_followed_games.html", context)
        add_never_cache_headers(response)
        return response


class NewsListView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """ "All news" -- what Home's own news card links to once there's more than
    NEWS_LIMIT items to show. Unlike Home's own teaser (team-relevant items
    only), this is the full browsable archive: every published, member-visible
    news item for the club, newest first, regardless of which team it's about.
    Same visibility rule as Home -- internal or both, never external-only
    (that's the public website's own audience, not this app's)."""

    template_name = "mobile/news_list.html"
    screen_title = _("News")
    active_tab = "news"
    PAGE_SIZE = 20
    EXCERPT_WORDS = 30

    def _is_partial(self):
        """The infinite-scroll sentinel (``#news-more`` in _news_cards.html) asks for just the next cards."""
        return bool(self.request.GET.get("page")) and self.request.headers.get("HX-Target") == "news-more"

    def get_template_names(self):
        if self._is_partial():
            return ["mobile/_news_cards.html"]
        return super().get_template_names()

    def get_context_data(self, **kwargs):
        news_items = member_news(self.request.club).prefetch_related("teams", "photos")
        page = Paginator(news_items, self.PAGE_SIZE).get_page(self.request.GET.get("page"))
        language = get_language()
        cards = []
        for item in page.object_list:
            title, body, _language = localized_news(item, language)
            photo = item.main_photo
            cards.append({"item": item, "title": title, "excerpt": render_body_excerpt(body, words=self.EXCERPT_WORDS), "photo": photo, "teams": ", ".join(team.short_name for team in item.teams.all())})
        context = super().get_context_data(page=page, cards=cards, **kwargs)
        return context


class NewsDetailView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M4 -- the design handoff's M4 section: a
    photo-hero permalink for a single published News item. Visibility mirrors
    news.tasks.notify_news_published's own gate -- PUBLISHED *and* actually
    live (published_at in the past) -- so a scheduled-but-not-yet-live item
    404s here exactly like it does everywhere else a member could reach it,
    rather than leaking a preview of it early.

    Language is Django's own active-language detection, not a member-facing
    toggle (unlike management's split-view Dutch/English editing tool) --
    English shows only when the request's active language actually is "en";
    everything else (including no active language at all) shows the native
    (Dutch) text.
    """

    template_name = "mobile/news_detail.html"
    screen_title = _("News")
    active_tab = "news"

    def get_context_data(self, **kwargs):
        news_item = get_object_or_404(live_news(self.request.club).prefetch_related("teams", "photos"), slug=self.kwargs["slug"])
        title, body, _language = localized_news(news_item, get_language())

        return super().get_context_data(
            screen_title=title,
            news_item=news_item,
            article_title=title,
            article_body=render_body_html(body),
            **kwargs,
        )


class MeView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M5 -- the design handoff's M5 section, "Me
    & my people". A header for ``self.me`` (member-since year plus a staff
    label, see below), a "People I manage" card (one row per managed_people,
    each with their current-season team + jersey number when they're on a
    roster), and a settings-ish card linking into M6 (edit_profile) for
    ``self.me`` and into M7 (notifications).

    The mockup's "Household & contacts" row and its "Coach mode" promo card
    have nowhere to lead in this build (no dedicated screen) and are
    deliberately omitted rather than built as dead or inert links.
    "Payments & dues" does lead somewhere -- PaymentsView below -- with its
    "N OPEN" pill only rendered once there's actually a balance owed.

    "Teams I coach/manage" is new, beyond the mockup: one row per current-season
    StaffAssignment self.me holds (team + position/role), each linking
    straight into Coach mode for that team (mobile:coach_today?team=<pk>,
    which mobile.coach_mixins.CoachScopeMixin's own ?team= handling already
    resolves and persists). Shown for any staffed team, not just ones
    self.me *manages* -- Coach mode's own screens already render read-only
    for a non-management position, so there's nothing to hide here.

    There's no license/eligibility field on Member or ClubMembership to power
    the mockup's "licence OK" text, so each managed person's meta line is
    real roster data instead: current-season team + jersey number when
    they're on one, nothing extra otherwise.
    """

    template_name = "mobile/me.html"
    screen_title = _("Me")
    active_tab = "me"

    def get_context_data(self, **kwargs):
        club = self.request.club
        season = current_season(club)

        member_since = None
        team_manager_label = None

        if self.me is not None and season is not None:
            membership = ClubMembership.objects.filter(club=club, member=self.me, season=season).first()
            if membership is not None:
                member_since = membership.created

        # PersonScopeMixin.get_context_data computes this same check for the
        # ``has_staff_access`` context var, but only as a context value, not
        # an attribute on self -- recomputed here since it's needed before
        # that runs.
        if self.me is not None and has_management_access(self.request.user, club):
            # teams_managed_by returns *every* club team for an ADMIN (see its
            # own docstring) -- fine for authority checks, but a wall of team
            # names makes a poor header subtitle, so it only gets spelled out
            # for someone managing a small, specific handful; anyone else
            # (including a full club admin) just reads as "Staff".
            managed_teams = list(teams_managed_by(self.request.user, club))
            if 1 <= len(managed_teams) <= 2:
                team_manager_label = _("Team manager %(teams)s") % {"teams": ", ".join(sorted(team.short_name for team in managed_teams))}
            else:
                team_manager_label = _("Staff")

        people_rows = []
        if self.managed_people:
            memberships_by_member = {}
            if season is not None:
                memberships_by_member = {membership.member_id: membership for membership in TeamMembership.objects.filter(member__in=self.managed_people, season=season).select_related("team")}
            people_rows = [{"person": person, "membership": memberships_by_member.get(person.pk)} for person in self.managed_people]

        # Same "not reviewed yet" filter PaymentsView/HomeView apply to this
        # same open_dues_rows result -- this badge count must not claim more
        # is owed than the Payments & dues screen it links to actually shows.
        awaiting_confirmation = membership_ids_awaiting_confirmation(club)
        open_dues_count = len([row for row in open_dues_rows(club, self.managed_people, season) if row["membership"].pk not in awaiting_confirmation])

        staff_assignments = []
        if self.me is not None and season is not None:
            staff_assignments = list(StaffAssignment.objects.filter(member=self.me, season=season).select_related("team", "position").order_by("team__name"))

        # Same family scoping as the "Add payment" voucher dropdown
        # (management.forms.AddPaymentForm) -- self.me plus anyone sharing a
        # Family with them (any role, both directions: Member.family_members),
        # not just managed_people's one-directional children. Only ever
        # usable vouchers show at all: expired, deactivated, or fully
        # consumed ones are excluded outright rather than shown with a
        # status pill -- nothing here can actually be spent.
        vouchers = []
        if self.me is not None:
            family_ids = {self.me.pk, *self.me.family_members(club).values_list("pk", flat=True)}
            vouchers = list(Voucher.objects.filter(club=club, issued_to__in=family_ids, is_active=True, expiry_date__gte=timezone.localdate(), consumed_amount__lt=F("amount")).select_related("issued_to").order_by("expiry_date"))

        # Just the count for the "Forms" menu row's own pill -- the full,
        # per-person list (formbuilder.services.audience.form_status_rows_for)
        # lives on its own page now, FormsListView below.
        open_forms_count = len([row for row in form_status_rows_for(self.managed_people, club) if row["submitted_at"] is None])

        # Gates the "Register" menu row -- no dead link when the club has no
        # active MEMBERSHIP-type product configured (registration.services.
        # pricing.available_registration_products), same "just absent, not
        # disabled" treatment as every other row here.
        registration_open = available_registration_products(club).exists()

        return super().get_context_data(
            member_since=member_since,
            team_manager_label=team_manager_label,
            live_game_updates_enabled=flag_is_active(self.request, "game_timeline"),
            people_rows=people_rows,
            open_dues_count=open_dues_count,
            staff_assignments=staff_assignments,
            vouchers=vouchers,
            open_forms_count=open_forms_count,
            registration_open=registration_open,
            has_multiple_clubs=has_multiple_clubs(self.request),
            available_languages=[get_language_info(code) for code, _name in settings.LANGUAGES],
            **kwargs,
        )


class FormsListView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """The "Forms" row off Me -- every form send self.managed_people are or
    were ever addressed to, one row per (send, member), pending or
    completed with its submitted date. Unbounded by is_active/opens_at/
    closes_at unlike Home's own "Forms to complete" card (formbuilder.
    services.audience.is_send_open): this is the full record, not just
    what's currently actionable."""

    template_name = "mobile/forms_list.html"
    screen_title = _("Forms")
    active_tab = "me"

    def get_context_data(self, **kwargs):
        forms_status = form_status_rows_for(self.managed_people, self.request.club)
        # "To do" = still open and not yet submitted; everything else (done, or closed without an
        # answer) goes under "Done and closed" -- a closed, unanswered row is shown but not tappable.
        for row in forms_status:
            row["is_done"] = row["submission"] is not None
            row["is_open"] = is_send_open(row["send"])
        todo_rows = [row for row in forms_status if not row["is_done"] and row["is_open"]]
        rest_rows = [row for row in forms_status if row["is_done"] or not row["is_open"]]
        return super().get_context_data(forms_status=forms_status, todo_rows=todo_rows, rest_rows=rest_rows, show_names=len(self.managed_people) > 1, **kwargs)


class FormFillView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """Renders and submits one FormSend's dynamic form -- reached from the
    Home "Forms to complete" card, the Me page's Forms section, or a form
    notification's own link (mobile.views._notification_source_link).

    Only reachable for a send addressed to the viewer or someone they manage
    (formbuilder.services.audience.addressed_people -- the same audience check
    submit_form runs, and the same person-scope the Forms list shows rows
    for); anyone else gets a 404, so a send's questions aren't readable by
    any member of the club who guesses its URL. Beyond that, GET always
    renders (even once closed/already submitted -- the window/quota checks
    live in formbuilder.services.submission.submit_form, run for real on
    POST, not duplicated here as a second, read-only copy that could drift
    from it); a rejected POST re-renders the
    same screen with the service layer's own field-level errors attached,
    the same "translate a service exception into notify() + stay on the
    screen" idiom ShopCheckoutView uses for CheckoutError, except re-render
    instead of redirect so those field errors are actually visible.

    A parent/guardian fills a form in *for* one addressed person
    (``for_member``): ``?for=<member id>`` picks who, and must be one of
    self.managed_people the send is addressed to (anything else 404s).
    Without it, the viewer's own response is the default when they're
    addressed themselves, otherwise the first addressed child. The
    submission is recorded for that person, with the viewer as
    submitted_by -- so audience and per-member limits are the child's."""

    template_name = "mobile/form_fill.html"
    screen_title = _("Form")
    active_tab = "me"

    def get_send(self):
        send = get_object_or_404(FormSend.objects.select_related("form").filter(club=self.request.club), pk=self.kwargs["pk"])
        self.addressed_people = addressed_people(send, self.managed_people)
        if not self.addressed_people:
            raise Http404("Form not addressed to you.")
        self.for_member = self._resolve_for_member()
        return send

    def _resolve_for_member(self):
        requested_id = self.request.GET.get("for")
        if requested_id:
            for person in self.addressed_people:
                if str(person.pk) == requested_id:
                    return person
            raise Http404("Form not addressed to that person.")
        if self.me in self.addressed_people:
            return self.me
        return self.addressed_people[0]

    def get(self, request, *args, **kwargs):
        send = self.get_send()
        return self.render_to_response(self.get_context_data(send=send, form=style_dynamic_form(build_form(send.form))))

    def post(self, request, *args, **kwargs):
        send = self.get_send()
        try:
            submit_form(send, self.for_member, request.POST, files=request.FILES, submitted_by=self.me)
        except FormSubmissionError as error:
            # Re-run the same validation Django's own full_clean() already did
            # once inside submit_form, rather than manually replaying
            # error.errors' messages onto a second bound_form via add_error():
            # add_error()'s first call lazily triggers full_clean() as a side
            # effect (it reads self.errors internally), which re-validates
            # every field itself -- so a manually copied "this field is
            # required" landed *on top of* Django's own identical one, twice.
            # Calling is_valid() up front here instead means there's only
            # ever one real validation pass, so nothing to duplicate.
            # error.errors is only non-empty for that field-level case; a
            # window/audience/quota/login rejection has no field to attach
            # to at all, so that's the one case still surfaced by hand.
            bound_form = style_dynamic_form(build_form(send.form, data=request.POST, files=request.FILES))
            bound_form.is_valid()
            if not error.errors:
                bound_form.add_error(None, str(error))
            notify(request, f"e|{_('Could not submit')}|{error}")
            return self.render_to_response(self.get_context_data(send=send, form=bound_form))

        notify(request, f"s|{_('Submitted')}|{_('Thanks -- your response has been recorded.')}")
        return HttpResponseRedirect(reverse("mobile:home"))

    def get_context_data(self, **kwargs):
        send = kwargs["send"]
        # Consecutive fields sharing a Field.section value sit under that heading (blank = none).
        section_by_key = dict(send.form.fields.values_list("key", "section"))
        sections = []
        for bound_field in kwargs["form"]:
            title = section_by_key.get(bound_field.name, "")
            if sections and sections[-1]["title"] == title:
                sections[-1]["fields"].append(bound_field)
            else:
                sections.append({"title": title, "fields": [bound_field]})
        return super().get_context_data(
            screen_title=kwargs["send"].form.title,
            sections=sections,
            send_is_open=is_send_open(send),
            addressed=self.addressed_people,
            for_member=self.for_member,
            # Named whenever it isn't just the viewer's own, only possible, response.
            show_for_member=self.for_member != self.me or len(self.addressed_people) > 1,
            fill_for_others=[person for person in self.addressed_people if person != self.for_member],
            **kwargs,
        )


class FormResponseView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """Read-only "my responses" screen for one already-submitted Submission --
    reached by tapping a "Completed" row on the Forms list (mobile:forms_list).
    Keyed by the submission itself, not the send, so there's no ambiguity
    about which of self.managed_people it belongs to the way a send-keyed
    URL would have. Shows every field on the form (not just Field.is_active
    ones -- a retired field an answer still references stays visible here,
    same "is_active only gates new sends" reasoning as FormFieldUpdateView's
    own docstring), each with the value actually recorded or "--" if it was
    left blank."""

    template_name = "mobile/form_response.html"
    screen_title = _("Your responses")
    active_tab = "me"

    def get_submission(self):
        managed_ids = {person.pk for person in self.managed_people}
        submission = get_object_or_404(Submission.objects.select_related("send__form", "member").filter(send__club=self.request.club), pk=self.kwargs["pk"])
        if submission.member_id not in managed_ids:
            raise Http404("Not one of your own submissions.")
        return submission

    def get_context_data(self, **kwargs):
        submission = self.get_submission()
        answers_by_field_id = {answer.field_id: answer.value for answer in submission.answers.all()}
        rows = [{"field": field, "display": _display_answer(answers_by_field_id.get(field.id))} for field in submission.send.form.fields.order_by("order")]
        return super().get_context_data(submission=submission, rows=rows, screen_title=submission.send.form.title, **kwargs)


def _display_answer(value):
    """Render a stored Answer.value for the read-only responses screen --
    None (a skipped optional field) and a CHECKBOX's bool need a word
    rather than Django's own str(), and a MULTICHOICE's list reads better
    comma-joined than as a Python repr."""
    if value is None:
        return None
    if value is True:
        return _("Yes")
    if value is False:
        return _("No")
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


class PaymentsView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M5's "Payments & dues" row -- every open season-dues balance across
    ``self.managed_people`` (club.services.fees.open_dues_rows is the shared
    source behind Home's own dues card too). No online payment gateway
    exists yet, so "Pay" is the same non-functional stub as Home's -- this
    screen's job is visibility ("what do I owe, and when"), not collection.

    A membership billed through a *confirmed* registration is grouped with
    every other managed person that same registration covers into one
    combined card (mobile/_dues_registration_receipt.html) -- one balance,
    one "pay early and save" figure, one invoice download, matching the one
    invoice that was actually sent, rather than a separate card per person
    with its own (and, once a manual discount is involved, inaccurate --
    see registration.services.invoicing.batch_early_payment_offer's own
    docstring) early-payment figure. A membership with no registration
    behind it at all (manually created) still gets its own plain card
    (mobile/_dues_receipt.html), unchanged."""

    template_name = "mobile/payments.html"
    screen_title = _("Payments & dues")
    active_tab = "me"

    def get_context_data(self, **kwargs):
        # Grouping and filtering live in mobile.services.payments, shared with the app API.
        display_rows = payments_overview(self.request.club, self.managed_people)
        cards = payment_cards(display_rows, me=self.me, show_names=len(self.managed_people) > 1)
        return super().get_context_data(display_rows=display_rows, cards=cards, payment_instructions=self.request.club.payment_instructions, **kwargs)


class RegistrationInvoicePdfView(PersonScopeMixin, LoginRequiredMixin, View):
    """Payments & dues' own "Download invoice" link -- same PDF as
    registration.views.RegistrationInvoiceView hands the family via the
    public, token-gated status page, and management.views.
    RegistrationInvoicePdfView hands staff; scoped here to
    self.managed_people instead, since a signed-in mobile session already is
    the credential. Mirrors ShopInvoiceView's own try/except pattern. 404s
    until staff has confirmed the invoice (RegistrationBatch.invoice_sent_at)
    -- in practice this is moot for a membership PaymentsView itself would
    show (it's filtered out until then), but a guessed/stale pk shouldn't
    hand out a document that doesn't exist yet either."""

    def get(self, request, *args, **kwargs):
        batch = get_object_or_404(RegistrationBatch.objects.filter(club=request.club, entries__membership__member__in=self.managed_people).distinct(), pk=kwargs["pk"])
        if batch.invoice_sent_at is None:
            raise Http404("This registration's invoice hasn't been confirmed yet.")

        try:
            pdf = batch_invoice_pdf(batch)
        except RegistrationInvoicePDFError as error:
            notify(request, f"e|{_('PDF unavailable')}|{error}")
            return HttpResponseRedirect(reverse("mobile:payments"))

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{request.club.slug}-registration-{batch.pk}.pdf"'
        return response


class DuesInvoicePdfView(PersonScopeMixin, LoginRequiredMixin, View):
    """Payments & dues' "Download invoice" for a membership billed by hand (no
    registration behind it): the same PDF club.services.invoicing.invoice_pdf
    mails out, scoped to self.managed_people so it is never another family's."""

    def get(self, request, *args, **kwargs):
        invoice = get_object_or_404(DuesInvoice.objects.select_related("membership__member"), pk=kwargs["pk"], club=request.club, membership__member__in=self.managed_people)
        try:
            pdf = dues_invoice_pdf(invoice)
        except DuesInvoicePDFError as error:
            notify(request, f"e|{_('PDF unavailable')}|{error}")
            return HttpResponseRedirect(reverse("mobile:payments"))

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{invoice.number or invoice.pk}.pdf"'
        return response


class EditProfileView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M6 -- the design handoff's M6 section,
    "Edit personal info". The design mock also shows a "National register
    no.", an "Address", an "Allergies / notes" field and a "share contact
    with team parents" consent toggle -- none of those exist on
    ``members.models.Member``, so they're simply not part of this screen;
    MemberProfileForm (mobile/forms.py) only covers the fields the model
    actually has. The mock's other consent toggle, "photos on club
    channels", now does have a backing field (``photo_public_consent``,
    gating ``Member.public_photo``) and is part of the form.

    Two more mock rows *do* have real backing data, both rendered read-only
    (never editable here -- family links and staff document review each live
    elsewhere in the platform, not on a member's own edit-info screen):
      - "Contact 1" becomes every one of ``Member.guardians`` (there can be
        more than one, unlike the mock's single row), each with its real
        FamilyMembership role (parent/guardian/other) rather than the mock's
        invented "mother".
      - The "Medical form missing" banner becomes a real, club-defined
        open-requirements list from club.services.onboarding.checklist_for,
        scoped to this person's *current-season* ClubMembership -- shown only
        when at least one active requirement is neither complete nor
        bypassed (an "informational" open requirement with no current-season
        membership at all just means the banner never renders).

    Authorization: the target Member (``member_id`` URL kwarg) must be one of
    ``self.managed_people`` -- anyone else 404s, on both GET and POST. A 404
    (not EventDetailView.post's 400) is the deliberate choice here: that 400
    is for a malformed *value* inside an otherwise-valid POST to a resource
    the requester can already see (the event); this is a different resource
    per person, named directly in the URL, so an unmanaged member should read
    as "no such page" exactly like Event/News already do for another club's
    objects elsewhere in this file, not as a submission-shaped error.
    """

    template_name = "mobile/edit_profile.html"
    screen_title = _("Edit info")
    active_tab = "me"

    def _target_member(self):
        member_id = str(self.kwargs["member_id"])
        member = next((person for person in self.managed_people if str(person.pk) == member_id), None)
        if member is None:
            raise Http404("You can't edit that profile.")
        return member

    def get(self, request, *args, **kwargs):
        member = self._target_member()
        form = MemberProfileForm(instance=member)
        return self.render_to_response(self.get_context_data(member=member, form=form))

    def post(self, request, *args, **kwargs):
        member = self._target_member()
        if request.POST.get("action") == "remove_photo":
            # Only the self-service profile picture; the club's official player picture isn't the member's to remove.
            if member.profile_photo:
                member.profile_photo.delete(save=True)
            notify(request, f"s|{_('Saved')}|{_('The photo was removed.')}")
            return HttpResponseRedirect(reverse("mobile:edit_profile", kwargs={"member_id": member.pk}))
        form = MemberProfileForm(request.POST, request.FILES, instance=member)
        if form.is_valid():
            form.save()
            title = _("Saved")
            body = _("%(name)s's info was updated.") % {"name": member.get_full_name()}
            notify(request, f"s|{title}|{body}")
            # Stay on the screen (iOS shows "Saved" on the button) rather than bouncing back to Me.
            return HttpResponseRedirect(f"{reverse('mobile:edit_profile', kwargs={'member_id': member.pk})}?saved=1")
        return self.render_to_response(self.get_context_data(member=member, form=form))

    def get_context_data(self, **kwargs):
        member = kwargs["member"]

        guardian_rows = [
            {"member": family_membership.member, "role": family_membership.get_role_display()}
            for family_membership in FamilyMembership.objects.filter(
                role__in=[FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN],
                # family__club: Member is global -- without this, a member also
                # belonging to another club would show that club's guardians here
                # (2026-09-12 leak).
                family__club=self.request.club,
                family__memberships__member=member,
                family__memberships__role=FamilyMembership.FamilyRole.CHILD,
            )
            .select_related("member")
            .distinct()
        ]

        open_requirement_names = []
        season = current_season(self.request.club)
        if season is not None:
            membership = ClubMembership.objects.filter(club=self.request.club, member=member, season=season).first()
            if membership is not None:
                open_requirement_names = [requirement.name for requirement, status in checklist_for(membership) if status is None or not (status.is_complete or status.is_bypassed)]

        team_rows = []
        if season is not None:
            team_rows = list(TeamMembership.objects.filter(team__club=self.request.club, member=member, season=season).select_related("team", "position").order_by("team__name"))

        return super().get_context_data(
            screen_title=member.get_full_name(),
            team_rows=team_rows,
            saved=self.request.GET.get("saved") == "1",
            has_profile_photo=bool(member.profile_photo),
            show_photo_consent=bool(member.player_photo) or member.photo_public_consent,
            guardian_rows=guardian_rows,
            open_requirement_names=open_requirement_names,
            open_requirement_summary=", ".join(open_requirement_names),
            **kwargs,
        )


class ReRegisterView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """Re-register self/managed_people for a new season -- reuses the exact
    same registration.services.submission.submit_registration the public
    registration page (registration.views.RegistrationView) uses, so the
    two can never disagree about what happens next (a new PENDING
    ClubMembership, straight into the existing Sign-up queue, no separate
    review gate of its own).

    One row per managed person by default (existing_member pre-filled, so
    re-registering never creates a duplicate Member), plus one blank row for
    someone not yet known to the club (e.g. a newborn sibling). Each
    person's card also has its own "Add another registration" button
    (mobile/templates/mobile/reregister.html's own extra_body script) that
    clones a second (third, ...) entry row scoped to that same person --
    a second team, or an additional role (player *and* referee), all on
    this one screen rather than a separate entry point. submit_registration
    already handles a member who already has a ClubMembership this season
    by adding to it rather than erroring -- see that function's own
    docstring. render_page groups the formset's forms back by their
    existing_member value (not position) precisely because a person can now
    carry more than one.

    Same two-step "calculate price, then confirm" flow as the public page,
    no client-side pricing logic."""

    template_name = "mobile/reregister.html"
    screen_title = _("Register")
    active_tab = "me"

    def get_season(self, request, data=None):
        source = data if data is not None else request.GET
        return resolve_chosen_season(request.club, source.get("season"))

    def get_formset(self, season, data):
        # Scoping and the "Include this person" meaning of is_contact: mobile.services.reregistration.
        formset = entry_formset(self.request.club, self.managed_people, season, data=data)
        for row in formset.forms:
            style_dynamic_form(row)
            self._word_like_the_app(row)
        return formset

    @staticmethod
    def _word_like_the_app(row):
        """The native app's empty choices ("Choose…", "No preference") on one entry row."""
        row.fields["product_variant"].empty_label = _("Choose…")
        row.fields["requested_team"].empty_label = _("No preference")
        return row

    def get_member_current_numbers(self, season):
        return member_current_numbers(self.request.club, self.managed_people, season)

    def render_season_picker(self, available_seasons):
        return self.render_to_response(self.get_context_data(registration_open=bool(available_seasons), needs_season_choice=True, available_seasons=available_seasons))

    def get_editor(self, season, entry=None, data=None, index=None):
        """The editor sheet's context: a form (blank, prefilled from ``entry``, or bound to ``data``) and which list row it edits."""
        if entry is None and data is None and self.managed_people:
            entry = {"existing_member": str(self.managed_people[0].pk)}  # the signed-in person first, "Someone new" one tap away
        return {"form": editor_form(self.request.club, self.managed_people, season, data=data, entry=entry), "index": index}

    def render_page(self, season, entries, entry_formset=None, priced_entries=None, season_error=None, editor=None):
        """``entries``: the draft list (reregistration.entries_state shape); ``entry_formset``: that list bound and validated, when it has been."""
        return self.render_to_response(
            self.get_context_data(
                entry_formset=entry_formset,
                entry_cards=entry_cards(entries, entry_formset, self.managed_people, self.request.club),
                priced_entries=priced_entries,
                # Same amount submit_registration actually charges per entry,
                # summed once -- see registration.views.RegistrationView.
                # render_page's own comment for why min_registrants_discount
                # specifically (not the conditional early-payment one).
                priced_total=sum((row["price"] - row["min_registrants_discount"] for _form, _entry, row in priced_entries), Decimal("0")) if priced_entries else None,
                # See registration.views.RegistrationView.render_page's own
                # comment -- what the total comes to if every entry with an
                # early-payment deadline is paid by its own date.
                priced_early_total=sum((row["price"] - row["min_registrants_discount"] - row["deadline_discount"] for _form, _entry, row in priced_entries), Decimal("0")) if priced_entries else None,
                season_error=season_error,
                registration_open=True,
                registration_season=season,
                max_entries=MAX_REGISTRATION_ENTRIES,
                member_current_numbers=self.get_member_current_numbers(season),
                can_add_entry=len(entries) < MAX_REGISTRATION_ENTRIES,
                editor=editor,
            )
        )

    def render_editor(self, season, entries, editor):
        """The editor sheet's own content for an htmx request, the whole page (sheet open) otherwise."""
        if self.request.headers.get("HX-Request") == "true" and self.request.headers.get("HX-Target") == "registration-editor-body":
            return TemplateResponse(self.request, "mobile/_registration_entry_editor.html", self.get_context_data(registration_season=season, editor=editor, entry_cards=entry_cards(entries, None, self.managed_people, self.request.club)))
        return self.render_page(season, entries, editor=editor)

    def get(self, request, *args, **kwargs):
        season, available_seasons = self.get_season(request)
        if season is None:
            return self.render_season_picker(available_seasons)
        return self.render_page(season, [])

    def post(self, request, *args, **kwargs):
        season, available_seasons = self.get_season(request, data=request.POST)
        if season is None:
            return self.render_season_picker(available_seasons)

        action = request.POST.get("action", "")
        verb, _sep, argument = action.partition(":")
        entries = entries_state(request.POST)
        if verb in EDITOR_ACTIONS:
            return self.handle_list_action(request, season, entries, verb, argument)

        entry_formset = self.get_formset(season, state_to_data(entries, season))
        if not entry_formset.is_valid():
            return self.render_page(season, entries, entry_formset)

        if action == "edit":
            # "Back" from the receipt screen -- the same entries, back in the list.
            return self.render_page(season, entries, entry_formset)

        try:
            built, priced = price_formset(entry_formset)
        except PricingError as error:
            return self.render_page(season, entries, entry_formset, season_error=str(error))

        current_numbers = self.get_member_current_numbers(season)
        priced_rows = priced_rows_with_jersey_fields(entry_formset, built, priced, team_number_pools(request.club, season), current_numbers)
        self._label_kept_jersey_numbers(priced_rows, current_numbers)

        if action != "submit":
            return self.render_page(season, entries, entry_formset, priced_entries=priced_rows)

        try:
            batch = submit_reregistration(request.club, request.user, self.me, built)
        except RegistrationError as error:
            return self.render_page(season, entries, entry_formset, season_error=str(error))

        send_registration_confirmation_email(batch, request=request)
        total = sum((row["price"] - row["min_registrants_discount"] for _form, _entry, row in priced_rows), Decimal("0"))
        title = _("Registration received")
        body = _("Thank you! The club will review it and send the invoice for € %(total)s. You'll find it under Payments & dues, and we've emailed you a confirmation.") % {"total": f"{total:.2f}"}
        notify(request, f"s|{title}|{body}")
        return HttpResponseRedirect(reverse("mobile:me"))

    def handle_list_action(self, request, season, entries, verb, argument):
        """add / change:N / refresh / save / cancel / remove:N -- editing the draft list; nothing is created until "Send registration"."""
        index = int(argument) if argument.isdigit() and int(argument) < len(entries) else None
        if verb == "remove":
            if index is not None:
                del entries[index]
            return self.render_page(season, entries)
        if verb == "cancel":
            return self.render_page(season, entries)
        if verb == "add":
            if len(entries) >= MAX_REGISTRATION_ENTRIES:
                too_many = ngettext("You can submit at most %(num)d registration at a time.", "You can submit at most %(num)d registrations at a time.", MAX_REGISTRATION_ENTRIES)
                return self.render_page(season, entries, season_error=too_many % {"num": MAX_REGISTRATION_ENTRIES})
            return self.render_editor(season, entries, self.get_editor(season))
        if verb == "change":
            if index is None:
                return self.render_page(season, entries)
            return self.render_editor(season, entries, self.get_editor(season, entry=entries[index], index=index))
        # refresh / save: the editor's own posted fields (the "editor-" prefix), plus which row it replaces.
        edited = request.POST.get("editor_index", "")
        edited_index = int(edited) if edited.isdigit() and int(edited) < len(entries) else None
        form = self.get_editor(season, data=request.POST, index=edited_index)
        if verb == "refresh":
            # A role/team/person change: redraw so the options and numbers follow it, without validating yet.
            return self.render_editor(season, entries, form)
        if not form["form"].is_valid():
            return self.render_page(season, entries, editor=form)
        entry = form["form"].to_entry()
        if edited_index is None:
            if len(entries) >= MAX_REGISTRATION_ENTRIES:
                return self.render_page(season, entries)
            entries.append(entry)
        else:
            entries[edited_index] = entry
        return self.render_page(season, entries)

    @staticmethod
    def _label_kept_jersey_numbers(priced_rows, current_numbers):
        """ "Keep #7" for the number the person already wears in the chosen team, "#8" for the rest."""
        for form, entry, _price in priced_rows:
            member = entry.existing_member
            team = entry.requested_team
            current = current_numbers.get(str(member.pk), {}).get(str(team.pk)) if member is not None and team is not None else None
            choices = [(value, label) for value, label in form.fields["requested_jersey_number"].choices if value != ""]
            labelled = [(value, _("Keep #%(number)s") % {"number": value} if current is not None and str(current) == value else f"#{value}") for value, _label in choices]
            form.fields["requested_jersey_number"].choices = [("", _("No number"))] + labelled


def _notification_source_link(notification):
    """(label, url) for a notification when it points at something this app has a
    page for -- (None, None) otherwise: its ``source`` object, or, for one without
    a source, its ``target_type`` (the calendar roll-up). One place both
    NotificationsView.get_context_data (the row's own label) and .post (the
    tap-to-mark-read redirect target) read from, so a new target only needs
    adding here."""
    source = notification.source
    if source is None and notification.target_type == "calendar":
        return _("Calendar"), reverse("mobile:calendar")
    if isinstance(source, Order):
        return _("Order"), reverse("mobile:shop_order_detail", kwargs={"pk": source.pk})
    if isinstance(source, News):
        return _("Club news"), reverse("mobile:news_detail", kwargs={"slug": source.slug})
    if isinstance(source, Event):
        return _("Event"), reverse("mobile:event_detail", kwargs={"pk": source.pk})
    if isinstance(source, FormSend):
        return _("Form"), reverse("mobile:form_fill", kwargs={"pk": source.pk})
    return None, None


class NotificationsView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """M7 -- the inbox, shaped like the iOS app's: one flat, newest-first list. An
    unread row has a red dot, a red left edge and a bold title; the subtitle is
    "person (only when this account manages more than one) · relative time". The
    title and body are shown in the active language (``Notification.text_for``).

    Paged (``PAGE_SIZE``) with an htmx "load more" sentinel; "Mark all read" and "Clear
    all" (confirmed in a sheet) are htmx actions that swap the list in place and answer
    with an ``HX-Trigger`` toast + the new unread count, so nothing reloads. Without
    htmx (plain POST) they redirect back like before. Tapping a row marks it read and,
    when its ``source`` resolves to a screen (see _notification_source_link), goes there.

    Scoped to every one of ``self.managed_people`` (not just scope_person) -- same scope
    PersonScopeMixin.get_context_data already uses for unread_notification_count, since
    notifications aren't really "per switched person" the way RSVPs are.
    """

    template_name = "mobile/notifications.html"
    screen_title = _("Notifications")
    active_tab = "me"
    PAGE_SIZE = 20

    def _is_htmx(self):
        return self.request.headers.get("HX-Request") == "true" and not self.request.headers.get("HX-Boosted")

    def get_template_names(self):
        if self._is_htmx() and self.request.headers.get("HX-Target") == "notif-more":
            return ["mobile/_notification_rows.html"]
        return super().get_template_names()

    def _partial(self, name, context):
        names = [name] if is_mobile_or_tablet(self.request) else [name.replace("mobile/", "mobile/desktop/", 1), name]
        return TemplateResponse(self.request, names, context)

    def _rows(self, notifications):
        language = (get_language() or "en").split("-")[0]
        names = {person.pk: person.first_name for person in self.managed_people}
        show_person = len(self.managed_people) > 1
        rows = []
        for notification in notifications:
            source_label, url = _notification_source_link(notification)
            text = notification.text_for(language)
            rows.append({"notification": notification, "title": text["title"], "body": text["body"], "source_label": source_label, "url": url, "person": names.get(notification.member_id) if show_person else None})
        return rows

    def get_context_data(self, **kwargs):
        page = None
        rows = []
        if self.managed_people:
            # GenericPrefetch batches `source` by content type (one query per type
            # actually used -- News/Event/FormSend) instead of one query per
            # notification the plain GenericForeignKey lookup would otherwise issue.
            notifications = (
                inbox_for(self.request.club, self.managed_people).select_related("member").prefetch_related(GenericPrefetch("source", [News.objects.all(), Event.objects.all(), FormSend.objects.all(), Order.objects.all()])).order_by("-created")
            )
            page = Paginator(notifications, self.PAGE_SIZE).get_page(self.request.GET.get("page"))
            rows = self._rows(page.object_list)
        return super().get_context_data(rows=rows, page=page, **kwargs)

    def _list_response(self, request, toast):
        """The refreshed list (htmx) plus a toast and the new unread count -- or a redirect without htmx."""
        if not self._is_htmx():
            return HttpResponseRedirect(reverse("mobile:notifications"))
        response = self._partial("mobile/_notification_list.html", self.get_context_data())
        response["HX-Trigger"] = json.dumps({"rc:toast": str(toast), "rc-unread": unread_count(request.club, self.managed_people)})
        return response

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")

        if action == "mark_all_read":
            mark_all_read(request.club, self.managed_people)
            return self._list_response(request, _("All notifications marked as read."))

        if action == "clear_all":
            # A hard delete, not another read-state flip -- "Clear" empties the
            # list itself, same as the clear-all gesture in a phone's own
            # notification centre, rather than just marking everything read.
            clear_all(request.club, self.managed_people)
            return self._list_response(request, _("Notifications cleared."))

        if action == "mark_read":
            notification = mark_read(request.club, self.managed_people, request.POST.get("notification_id"))
            if notification is None:
                return HttpResponseBadRequest(_("You can't mark that notification as read."))

            _label, url = _notification_source_link(notification)
            if self._is_htmx() and not url:
                # Nowhere to go: just flip the row to "read" in place.
                response = self._partial("mobile/_notification_row.html", {"row": self._rows([notification])[0]})
                response["HX-Trigger"] = json.dumps({"rc-unread": unread_count(request.club, self.managed_people)})
                return response
            return HttpResponseRedirect(url or reverse("mobile:notifications"))

        return HttpResponseBadRequest(_("Unknown action."))


class SecurityView(PersonScopeMixin, LoginRequiredMixin, TemplateView):
    """The "Security" row under Me (iOS SecurityView): the account's passkeys with Add /
    Rename / Remove, a link to change the password, and a read-only note on two-factor
    status. Adding, renaming and removing run through allauth's own webauthn screens
    (they re-authenticate when needed and already wear the app's shell via
    mobile/_auth_base.html); this page is the listing that ties them together."""

    template_name = "mobile/security.html"
    screen_title = _("Security")
    active_tab = "me"

    def get_context_data(self, **kwargs):
        authenticators = list(Authenticator.objects.filter(user=self.request.user).order_by("created_at"))
        passkeys = [
            {"pk": authenticator.pk, "name": authenticator.wrap().name, "created_at": authenticator.created_at, "last_used_at": authenticator.last_used_at} for authenticator in authenticators if authenticator.type == Authenticator.Type.WEBAUTHN
        ]
        return super().get_context_data(
            passkeys=passkeys,
            passkeys_enabled="webauthn" in settings.MFA_SUPPORTED_TYPES,
            has_totp=any(authenticator.type == Authenticator.Type.TOTP for authenticator in authenticators),
            **kwargs,
        )


#: Pill styling for BugListView's own report list -- SUBMITTED and WONT_FIX
#: both read as "nothing to act on right now" (neutral), IN_PROGRESS as an
#: open, in-flight state (warn), FIXED as resolved (ok). Never danger: a
#: reporter's own bug isn't an urgent/overdue signal the way an unpaid dues
#: balance is.
BUG_STATUS_PILL_CLASSES = {
    BugReport.Status.SUBMITTED: "pill-neutral",
    BugReport.Status.IN_PROGRESS: "pill-warn",
    BugReport.Status.FIXED: "pill-ok",
    BugReport.Status.WONT_FIX: "pill-neutral",
}


class BugListView(DesktopTemplateMixin, MemberAppMixin, TemplateView):
    """ "Report a bug" row on the Me screen (mobile/templates/mobile/me.html) --
    files into the standalone ``bugs`` app (bugs.services.file_report), which
    already handles admin notification, so this view is just a form + the
    reporter's own history.

    Scoped to the signed-in account itself, not PersonScopeMixin's family/
    "scope" concept -- a bug report isn't filed on behalf of a managed child --
    so this resolves ``self.me`` the same one-liner way mixins.py:49 does,
    rather than pulling in the full mixin.

    Template-visible fields are deliberately narrow -- see BugReport's own
    docstring: title, description, submitted date (created), status and notes
    (body/created, never note.author) always; fixed_at/fixed_version only once
    status is FIXED. priority, club and reported_by never reach the template.
    """

    template_name = "mobile/bug_list.html"
    screen_title = _("Report a bug")
    active_tab = "me"

    def prepare_member_app(self, request):
        super().prepare_member_app(request)
        self.me = Member.objects.filter(user=request.user).first()

    def _build_form(self, data=None):
        form = BugReportForm(data)
        form.fields["title"].widget.attrs["class"] = _INPUT_CLASSES
        form.fields["description"].widget.attrs["class"] = _TEXTAREA_CLASSES
        return form

    def get(self, request, *args, **kwargs):
        return self.render_to_response(self.get_context_data(form=self._build_form()))

    def post(self, request, *args, **kwargs):
        # BugReport.reported_by is a required FK -- with no Member record
        # linked to this account there's nothing to file the report as, and
        # the form is never rendered in that state either (see bug_list.html),
        # so a POST reaching here regardless (a stale tab, a replayed request)
        # is simply ignored rather than raising on the not-null constraint.
        if self.me is None:
            return HttpResponseRedirect(reverse("mobile:bug_list"))

        form = self._build_form(request.POST)
        if form.is_valid():
            file_report(club=request.club, reported_by=self.me, title=form.cleaned_data["title"], description=form.cleaned_data["description"])
            return HttpResponseRedirect(reverse("mobile:bug_list"))
        return self.render_to_response(self.get_context_data(form=form))

    def get_context_data(self, **kwargs):
        bugs = BugReport.objects.filter(club=self.request.club, reported_by=self.me).order_by("-created").prefetch_related("notes") if self.me is not None else []
        rows = [{"bug": bug, "pill_class": BUG_STATUS_PILL_CLASSES.get(bug.status, "pill-neutral")} for bug in bugs]
        unread_notification_count = unread_count(self.request.club, [self.me] if self.me is not None else [])
        # PersonScopeMixin isn't in this view's MRO (see the class docstring), so
        # unlike every other Member-mode screen it doesn't get any of its
        # get_context_data's shell context for free -- the base template (both
        # the mobile and desktop shells) needs screen_title/active_tab/
        # has_staff_access/season/unread_notification_count/vapid_public_key/
        # shop_open regardless of this view's own narrower "just self.me" scope,
        # so they're computed here the same way PersonScopeMixin does.
        return super().get_context_data(
            me=self.me,
            rows=rows,
            screen_title=self.screen_title,
            active_tab=self.active_tab,
            has_staff_access=self.me is not None and has_management_access(self.request.user, self.request.club),
            unread_notification_count=unread_notification_count,
            season=get_current_season(self.request),
            vapid_public_key=settings.VAPID_PUBLIC_KEY,
            shop_open=shop_enabled(self.request),
            **kwargs,
        )


#: Order payment line (small grey text under the total) and the member-facing progress pill,
#: worded like the native app. The progress pill is the only coloured one: warm while the club is
#: still working on it, green once it can be collected, red when cancelled.
ORDER_PAYMENT_LABELS = {
    Order.PaymentStatus.PENDING: _("To pay at pickup"),
    Order.PaymentStatus.PARTIALLY_PAID: _("Partly paid"),
    Order.PaymentStatus.PAID: _("Paid"),
    Order.PaymentStatus.REFUNDED: _("Refunded"),
}
ORDER_STATUS_LABELS = {
    Order.MemberStatus.NOT_READY: _("Being prepared"),
    Order.MemberStatus.IN_PRODUCTION: _("In production"),
    Order.MemberStatus.READY_FOR_PICKUP: _("Ready for pickup"),
    Order.MemberStatus.COMPLETED: _("Completed"),
    Order.MemberStatus.CANCELLED: _("Cancelled"),
}
ORDER_MEMBER_STATUS_PILL_CLASSES = {
    Order.MemberStatus.NOT_READY: "pill-warn",
    Order.MemberStatus.IN_PRODUCTION: "pill-warn",
    Order.MemberStatus.READY_FOR_PICKUP: "pill-ok",
    Order.MemberStatus.COMPLETED: "pill-ok",
    Order.MemberStatus.CANCELLED: "pill-danger",
}

#: Session key ShopCheckoutView leaves for ShopOrderDetailView's "Order placed. Thank you!" banner
#: (a query string would break the plain redirect the checkout has always answered with).
ORDER_PLACED_SESSION_KEY = "shop_order_placed"


def _is_partial_request(request) -> bool:
    """An htmx request aimed at one region (not a boosted full-page navigation)."""
    return request.headers.get("HX-Request") == "true" and not request.headers.get("HX-Boosted")


def _shop_line_detail(line, *, me, show_people) -> str:
    """ "For Lars · #17 SOMERS" for a cart/order line: who it is for (only when the account manages
    several people) and what is printed on it."""
    parts = []
    if show_people and line.beneficiary_id:
        name = _("Me") if me is not None and line.beneficiary_id == me.pk else line.beneficiary.first_name
        parts.append(_("For %(name)s") % {"name": name})
    printed = " ".join(bit for bit in (f"#{line.personalization_number}" if line.personalization_number else "", line.personalization_name.upper()) if bit)
    if printed:
        parts.append(printed)
    return " · ".join(str(part) for part in parts)


def _product_description_html(text):
    """A product's description as safe HTML: staff may have entered HTML (sanitised like news
    bodies) or plain text (escaped, line breaks kept)."""
    if "<" in text:
        return mark_safe(render_body_html(text))
    return mark_safe(linebreaks(text, autoescape=True))


class ShopHomeView(ShopScopeMixin, LoginRequiredMixin, TemplateView):
    """Shop tab landing -- every active+public Product for this club as a two-column grid, with
    "All" + category filter chips (client-side, Alpine). Reachable while the shop is closed too
    (``allow_closed``): it then shows the closed state with a way to "My orders", like the app."""

    template_name = "mobile/shop_home.html"
    screen_title = _("Shop")
    active_tab = "shop"
    allow_closed = True

    def get_context_data(self, **kwargs):
        if not shop_enabled(self.request):
            return super().get_context_data(shop_closed=True, products=[], categories=[], cart_item_count=0, **kwargs)

        # MEMBERSHIP-type products are never orderable through the shop --
        # they're priced through registration/re-registration instead
        # (registration.services.pricing) -- excluded structurally here
        # regardless of is_public, not left to staff remembering to flag
        # each one.
        products = annotate_availability(catalog(self.request.club).prefetch_related("variants"))
        for product in products:
            prices = [variant.effective_price for variant in product.variants.all() if variant.is_active] or [product.price]
            product.lowest_price = min(prices)
            product.has_price_range = len(set(prices)) > 1
            product.sold_out = not product.in_stock

        used_categories = {product.category_id for product in products if product.category_id}
        categories = list(ProductCategory.objects.filter(club=self.request.club, pk__in=used_categories).order_by("name"))
        cart = open_cart(self.request.club, self.request.user)
        cart_item_count = (cart.items.aggregate(total=Sum("quantity"))["total"] or 0) if cart is not None else 0
        # The desktop grid still sections the catalogue by category (mobile filters client-side).
        groups = [{"category": category, "products": [product for product in products if product.category_id == category.pk]} for category in categories]
        uncategorized = [product for product in products if product.category_id is None]
        if uncategorized:
            groups.append({"category": None, "products": uncategorized})
        return super().get_context_data(shop_closed=False, products=products, categories=categories, groups=groups, cart_item_count=cart_item_count, **kwargs)


class ShopProductDetailView(ShopScopeMixin, LoginRequiredMixin, TemplateView):
    """Photo/description, an option picker (when the product has any -- see ProductVariant's own
    docstring for why it's one free-text label, not separate size/colour axes), a "For" picker
    (only once the account manages more than one person), number/name personalisation, a quantity
    stepper and "Add to cart". The price follows the chosen option.

    "Add to cart" get-or-creates the member's open Cart for this club and bumps quantity on the
    (cart, product, variant, beneficiary) row if it already exists. From the page itself (htmx) it
    answers with a small "Added to your cart / View cart" confirmation (or the reason it
    couldn't); without htmx it redirects to the cart.
    """

    template_name = "mobile/shop_product_detail.html"
    screen_title = _("Shop")
    active_tab = "shop"

    def _product(self):
        return get_object_or_404(catalog(self.request.club), slug=self.kwargs["slug"])

    def get(self, request, *args, **kwargs):
        product = annotate_availability([self._product()])[0]
        variants = [variant for variant in product.variants.all() if variant.is_active]
        first_in_stock_variant = next((variant for variant in variants if variant.in_stock), None)
        prices = [variant.effective_price for variant in variants] or [product.price]
        variant_data = [{"id": str(variant.pk), "name": variant.name, "price": str(variant.effective_price), "in_stock": variant.in_stock} for variant in variants]
        return self.render_to_response(
            self.get_context_data(
                product=product,
                variants=variants,
                first_in_stock_variant=first_in_stock_variant,
                variant_data=variant_data,
                lowest_price=min(prices),
                description_html=_product_description_html(product.description) if product.description else "",
            )
        )

    def get_context_data(self, **kwargs):
        return super().get_context_data(screen_title=kwargs["product"].name, max_quantity=MAX_CART_QUANTITY, **kwargs)

    def _added(self, request, *, error=None):
        return render(request, "mobile/_shop_added.html", {"error": error})

    def post(self, request, *args, **kwargs):
        product = self._product()
        if self.me is None:
            return HttpResponseBadRequest(_("No member record for this account."))

        try:
            quantity = int(request.POST.get("quantity", 1))
        except TypeError, ValueError:
            quantity = 1
        quantity = min(max(1, quantity), MAX_CART_QUANTITY)

        variant = None
        variant_id = request.POST.get("variant")
        if variant_id:
            variant = product.variants.filter(pk=variant_id, is_active=True).first()
            if variant is None:
                return HttpResponseBadRequest(_("That option isn't available."))

        beneficiary = None
        beneficiary_id = request.POST.get("beneficiary")
        if beneficiary_id:
            beneficiary = next((person for person in self.managed_people if str(person.pk) == beneficiary_id), None)
            if beneficiary is None:
                return HttpResponseBadRequest(_("You can't order for that person."))

        # Personalisation only sticks on a product that offers it (shop.services.cart).
        try:
            add_to_cart(
                request.club,
                request.user,
                product,
                quantity=quantity,
                variant=variant,
                beneficiary=beneficiary,
                personalization_number=request.POST.get("personalization_number", ""),
                personalization_name=request.POST.get("personalization_name", ""),
            )
        except CheckoutError as error:
            if _is_partial_request(request):
                return self._added(request, error=str(error))
            notify(request, f"w|{_('Not available')}|{error}")
            return HttpResponseRedirect(request.path)

        title = _("Added to cart")
        body = _("%(product)s added to your cart.") % {"product": product.name}
        if _is_partial_request(request):
            # A toast, shown without leaving the page.
            response = HttpResponse(status=204)
            response["HX-Trigger"] = json.dumps({"rc:toast": str(body)})
            return response
        notify(request, f"s|{title}|{body}")
        return HttpResponseRedirect(reverse("mobile:shop_cart"))


def _cart_context(view, request, code=""):
    """What shop/_shop_cart_body.html (and the full cart page) render: the cart re-priced to what
    checkout would charge, with each line's own problem message and "For X · #17 NAME" detail."""
    summary = cart_summary(request.club, request.user, code=code, member=view.me, ip=client_ip(request))
    items = summary["items"]
    annotate_cart_variants(items)
    show_people = len(view.managed_people) > 1
    for item in items:
        # A sold-out option can only be removed; too large a quantity can be lowered.
        item.sold_out_option = item.variant_id is not None and item.variant.in_stock is False
        item.detail = _shop_line_detail(item, me=view.me, show_people=show_people)
        if not item.unavailable:
            item.problem = ""
        elif item.sold_out_option:
            item.problem = _("Sold out")
        elif item.variant_id is not None:
            item.problem = _("Not enough in stock for this quantity. Lower it or remove the item.")
        else:
            item.problem = _("Sold out or no longer available")
    return {
        "cart": summary["cart"],
        "items": items,
        "code": summary["code"],
        "discount": summary["discount"],
        "discount_error": summary["discount_error"],
        "totals": summary["totals"],
        "has_unavailable": summary["has_unavailable"],
        "prices_changed": summary["prices_changed"],
        "max_quantity": MAX_CART_QUANTITY,
    }


class ShopCartView(ShopScopeMixin, LoginRequiredMixin, TemplateView):
    """Cart + checkout screen. The discount code is applied by POST (htmx swaps the cart body in
    place; without htmx it redirects to ``?code=``, which still previews a code on a plain GET)
    and recomputed through shop.services.pricing.cart_totals. "Place order" carries whatever code
    is currently applied through to ShopCheckoutView as a hidden field; place_order is the only
    place a code is actually redeemed, so it re-validates it itself rather than trusting this
    screen's own preview.

    Every line is re-priced from its current product/variant on each render
    (shop.services.checkout.reprice_cart_items), so the cart always shows what checkout will
    actually charge; lines that can't be ordered any more say why and block "Place order". A
    wrong code here counts towards the same per-member/per-IP throttle as one at checkout.
    """

    template_name = "mobile/shop_cart.html"
    screen_title = _("Cart")
    active_tab = "shop"

    def get_context_data(self, **kwargs):
        return super().get_context_data(**_cart_context(self, self.request, self.request.GET.get("code", "")), **kwargs)

    def post(self, request, *args, **kwargs):
        code = request.POST.get("code", "").strip()
        if _is_partial_request(request):
            return render(request, "mobile/_shop_cart_body.html", _cart_context(self, request, code))
        return HttpResponseRedirect(f"{reverse('mobile:shop_cart')}?{urlencode({'code': code})}" if code else reverse("mobile:shop_cart"))


class ShopCartItemUpdateView(ShopScopeMixin, LoginRequiredMixin, View):
    """+/- and remove on a single cart row. Decrementing below 1 removes the item outright -- a
    row stuck at a quantity nobody asked to keep isn't useful, and there's no separate "0 means
    gone" state to represent. Via htmx it answers with the refreshed cart body (carrying the
    applied discount ``code`` along), otherwise it redirects to the cart."""

    def post(self, request, *args, **kwargs):
        item = get_object_or_404(CartItem, pk=kwargs["item_id"], cart__club=request.club, cart__user=request.user, cart__status=Cart.CartStatus.OPEN)
        action = request.POST.get("action")

        if action == "increment":
            set_quantity(item, item.quantity + 1)
        elif action == "decrement":
            set_quantity(item, item.quantity - 1)
        elif action == "remove":
            set_quantity(item, 0)
        else:
            return HttpResponseBadRequest(_("Unknown action."))

        if _is_partial_request(request):
            return render(request, "mobile/_shop_cart_body.html", _cart_context(self, request, request.POST.get("code", "")))
        return HttpResponseRedirect(reverse("mobile:shop_cart"))


class ShopCheckoutView(ShopScopeMixin, LoginRequiredMixin, View):
    """POST-only target for the cart screen's "Place order" button. Every
    checkout rule (shop open, cart non-empty, a real discount code) lives in
    shop.services.checkout.place_order -- this view's only job is to call it
    and translate CheckoutError into a flashed message instead of a 500."""

    def post(self, request, *args, **kwargs):
        cart = open_cart(request.club, request.user)
        if cart is None or self.me is None:
            notify(request, f"e|{_('Nothing to order')}|{_('Your cart is empty.')}")
            return HttpResponseRedirect(reverse("mobile:shop_cart"))

        try:
            order = place_order(cart, purchaser=self.me, discount_code=request.POST.get("discount_code", ""), ip=client_ip(request))
        except CheckoutError as error:
            notify(request, f"e|{_('Could not place order')}|{error}")
            return HttpResponseRedirect(reverse("mobile:shop_cart"))

        title = _("Order placed")
        body = _("Order %(number)s is in -- pay when you pick it up.") % {"number": order.number}
        notify(request, f"s|{title}|{body}")
        request.session[ORDER_PLACED_SESSION_KEY] = str(order.pk)
        return HttpResponseRedirect(reverse("mobile:shop_order_detail", kwargs={"pk": order.pk}))


class ShopOrdersView(ShopScopeMixin, LoginRequiredMixin, TemplateView):
    """ "My orders" -- every past Order across self.managed_people, not just
    self.me. Order.purchaser is always whoever's own login placed it (this
    app gives one Cart per account, never per managed person), so this is the
    same "aggregate across everyone I'm responsible for" scope PaymentsView/
    club.services.fees.open_dues_rows already use, e.g. a parent checking on
    an order a teenager placed under their own login. Also reachable while
    the shop is closed."""

    template_name = "mobile/shop_orders.html"
    screen_title = _("My orders")
    active_tab = "shop"
    allow_closed = True

    def get_context_data(self, **kwargs):
        orders = orders_for(self.request.club, self.managed_people)
        rows = [
            {
                "order": order,
                "payment_label": ORDER_PAYMENT_LABELS.get(order.payment_status, ORDER_PAYMENT_LABELS[Order.PaymentStatus.PENDING]),
                "status_label": ORDER_STATUS_LABELS.get(order.member_status, ORDER_STATUS_LABELS[Order.MemberStatus.NOT_READY]),
                "status_pill_class": ORDER_MEMBER_STATUS_PILL_CLASSES.get(order.member_status, "pill-warn"),
            }
            for order in orders
        ]
        return super().get_context_data(rows=rows, **kwargs)


class ShopOrderDetailView(ShopScopeMixin, LoginRequiredMixin, TemplateView):
    """Number, date, status, lines, discounts, total, pickup instructions and the invoice PDF.
    Scoped to self.managed_people, same reasoning as ShopOrdersView -- an order outside that set
    404s, same treatment EditProfileView gives an unmanaged member. Straight after checkout it
    carries an "Order placed. Thank you!" banner (``is_new``, once)."""

    template_name = "mobile/shop_order_detail.html"
    screen_title = _("Order")
    active_tab = "shop"
    allow_closed = True

    def get_context_data(self, **kwargs):
        order = get_object_or_404(Order.objects.select_related("purchaser"), pk=self.kwargs["pk"], club=self.request.club, purchaser__in=self.managed_people)
        show_people = len(self.managed_people) > 1
        lines = list(order.order_items.select_related("product", "variant", "beneficiary").order_by("created"))
        for line in lines:
            line.detail = _shop_line_detail(line, me=self.me, show_people=show_people)
        is_new = self.request.session.pop(ORDER_PLACED_SESSION_KEY, None) == str(order.pk)
        return super().get_context_data(
            screen_title=order.number,
            order=order,
            lines=lines,
            discounts=list(order.applied_discounts.select_related("discount")),
            is_new=is_new,
            payment_label=ORDER_PAYMENT_LABELS.get(order.payment_status, ORDER_PAYMENT_LABELS[Order.PaymentStatus.PENDING]),
            status_label=ORDER_STATUS_LABELS.get(order.member_status, ORDER_STATUS_LABELS[Order.MemberStatus.NOT_READY]),
            status_pill_class=ORDER_MEMBER_STATUS_PILL_CLASSES.get(order.member_status, "pill-warn"),
            **kwargs,
        )


class ShopInvoiceView(ShopScopeMixin, LoginRequiredMixin, View):
    """Rendered on demand via shop.services.invoices.render_invoice_pdf --
    mirrors management.views.DuesInvoicePdfView's own try/except pattern.
    Scoped the same way as ShopOrdersView/ShopOrderDetailView -- never
    another member's invoice, including another managed family's."""

    allow_closed = True

    def get(self, request, *args, **kwargs):
        order = get_object_or_404(Order, pk=kwargs["pk"], club=request.club, purchaser__in=self.managed_people)
        invoice = getattr(order, "invoice", None)
        if invoice is None:
            raise Http404("No invoice for that order.")

        try:
            pdf = render_invoice_pdf(invoice)
        except ShopInvoicePDFError as error:
            notify(request, f"e|{_('PDF unavailable')}|{error}")
            return HttpResponseRedirect(reverse("mobile:shop_order_detail", kwargs={"pk": order.pk}))

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{invoice.number}.pdf"'
        return response
