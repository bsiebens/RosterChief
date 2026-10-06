"""What the member app's Home screen shows for a set of managed people -- shared by
mobile.views.HomeView and the app API's ``GET /home`` so both agree on every card.

``home_summary`` returns model objects; each caller shapes them (template context,
JSON). Every card is independently optional: an empty screen is all of them empty.
"""

from django.db.models import Count, F, Q, Window
from django.db.models.functions import RowNumber
from django.utils import timezone

from club.services.access import current_season
from club.services.fees import open_dues_rows
from events.models import Attendance, EventTask, OfficialSignup, RefereeSignup
from events.services.rsvp import rsvp_is_closed
from formbuilder.services import form_status_rows_for, is_send_open
from news.services import member_news
from registration.services.invoicing import membership_ids_awaiting_confirmation

#: Caps keep Home to one screenful; the Calendar, News and Forms screens list everything.
NEEDS_ANSWER_LIMIT = 5
OPEN_TASKS_LIMIT = 5
NEWS_LIMIT = 3
FORMS_LIMIT = 5


def _first_per_member(queryset):
    """The soonest row of ``queryset`` for each member (ties: lowest event id)."""
    ranked = queryset.annotate(rank=Window(RowNumber(), partition_by=F("member_id"), order_by=[F("event__start").asc(), F("event_id").asc()])).filter(rank=1)
    return list(ranked)


def _group_cards(kind, attendances):
    """One card per event: everyone whose soonest row of this kind is the same event shares it."""
    by_event = {}
    for attendance in attendances:
        by_event.setdefault(attendance.event_id, []).append(attendance)
    cards = [{"kind": kind, "event": rows[0].event, "attendances": sorted(rows, key=lambda row: (row.member.first_name, row.member.last_name))} for rows in by_event.values()]
    return sorted(cards, key=lambda card: (card["event"].start, card["event"].pk))


def home_summary(club, people, *, officials_enabled: bool, exclude_card_rows: bool = False, now=None) -> dict:
    """Keys:

    * ``hero_attendance`` / ``rsvp_closed``: the soonest upcoming invitation nobody has
      answered yet, and whether its deadline has passed (shown read-only then);
    * ``going_attendance``: the soonest event someone is going to (PRESENT or SELECTED);
    * ``member_cards``: per person, their soonest still-answerable unanswered invitation
      (``rsvp_needed``) and their soonest going/selected event (``next_going``), grouped so that
      people with the same event share one card: ``{"kind", "event", "attendances"}``, all
      ``rsvp_needed`` cards first, each group by event start. Unlike the single hero, an
      invitation whose deadline has passed is skipped, so every card can be answered;
    * ``needs_answer`` (capped) / ``needs_answer_total``: rows ``{"kind": "rsvp"|"referee"|
      "official", "event", "member", "attendance"|"signup"}`` still owing an answer and still
      answerable, excluding the hero -- and, with ``exclude_card_rows`` (the app API, which
      shows them on ``member_cards`` instead), every ``rsvp_needed`` card's invitation too;
    * ``open_tasks`` (capped, each with ``open_count``) / ``open_tasks_total``: unclaimed
      task slots on the people's upcoming events;
    * ``dues_rows``: club.services.fees.open_dues_rows, minus balances nobody has
      reviewed yet (the same filter as Payments);
    * ``news_items``: the latest member-visible news;
    * ``forms_to_complete``: open, unsubmitted form rows, one per (send, person).
    """
    people = list(people)
    summary = {
        "hero_attendance": None,
        "rsvp_closed": False,
        "going_attendance": None,
        "member_cards": [],
        "needs_answer": [],
        "needs_answer_total": 0,
        "open_tasks": [],
        "open_tasks_total": 0,
        "dues_rows": [],
        "news_items": [],
        "forms_to_complete": [],
    }
    if not people:
        return summary
    now = now or timezone.now()

    upcoming = Attendance.objects.filter(member__in=people, event__club=club, event__cancelled=False, event__start__gte=now).select_related("event", "event__location", "member")

    hero = upcoming.filter(status=Attendance.AttendanceStatus.NO_RESPONSE).order_by("event__start").first()
    summary["hero_attendance"] = hero
    summary["rsvp_closed"] = hero is not None and rsvp_is_closed(hero.event, now)
    summary["going_attendance"] = upcoming.filter(status__in=[Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.SELECTED]).order_by("event__start").first()

    answerable = Q(event__deadline__isnull=True) | Q(event__deadline__gte=now)
    rsvp_rows = _first_per_member(upcoming.filter(status=Attendance.AttendanceStatus.NO_RESPONSE).filter(answerable).select_related("event__opponent"))
    going_rows = _first_per_member(upcoming.filter(status__in=[Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.SELECTED]).select_related("event__opponent"))
    summary["member_cards"] = _group_cards("rsvp_needed", rsvp_rows) + _group_cards("next_going", going_rows)

    # Past the deadline a reply is no longer possible, so it doesn't belong in "still
    # needs a reply" -- unlike the hero, which shows a closed one read-only.
    needs_answer_qs = upcoming.filter(status__in=[Attendance.AttendanceStatus.NO_RESPONSE, Attendance.AttendanceStatus.MAYBE]).filter(Q(event__deadline__isnull=True) | Q(event__deadline__gte=now))
    if hero is not None:
        needs_answer_qs = needs_answer_qs.exclude(pk=hero.pk)
    if exclude_card_rows:
        needs_answer_qs = needs_answer_qs.exclude(pk__in=[row.pk for row in rsvp_rows])
    needs_answer = [{"kind": "rsvp", "event": attendance.event, "member": attendance.member, "attendance": attendance} for attendance in needs_answer_qs]

    # Referee/official invites are the same kind of ask (a yes/no owed), so they share the
    # card. Only INVITED: an accepted one already has its answer.
    invite_filter = {"member__in": people, "event__club": club, "event__cancelled": False, "event__start__gte": now}
    referee_invites = RefereeSignup.objects.filter(**invite_filter, status=RefereeSignup.Status.INVITED).select_related("event", "member")
    needs_answer += [{"kind": "referee", "event": signup.event, "member": signup.member, "signup": signup} for signup in referee_invites]
    if officials_enabled:
        official_invites = OfficialSignup.objects.filter(**invite_filter, status=OfficialSignup.Status.INVITED).select_related("event", "member")
        needs_answer += [{"kind": "official", "event": signup.event, "member": signup.member, "signup": signup} for signup in official_invites]
    needs_answer.sort(key=lambda row: row["event"].start)
    summary["needs_answer_total"] = len(needs_answer)
    summary["needs_answer"] = needs_answer[:NEEDS_ANSWER_LIMIT]

    # A task is a slot anyone could take, not a reply owed -- its own card, one row per
    # open task on the same upcoming events.
    open_tasks_qs = EventTask.objects.filter(event_id__in=upcoming.values_list("event_id", flat=True).distinct()).annotate(claim_count=Count("claims")).filter(claim_count__lt=F("needed_quantity")).select_related("event").order_by("event__start")
    summary["open_tasks_total"] = open_tasks_qs.count()
    open_tasks = list(open_tasks_qs[:OPEN_TASKS_LIMIT])
    for task in open_tasks:
        task.open_count = task.needed_quantity - task.claim_count
    summary["open_tasks"] = open_tasks

    season = current_season(club)
    if season is not None:
        awaiting_confirmation = membership_ids_awaiting_confirmation(club)
        summary["dues_rows"] = [row for row in open_dues_rows(club, people, season) if row["membership"].pk not in awaiting_confirmation]

    summary["news_items"] = list(member_news(club, now=now).select_related("created_by").prefetch_related("teams", "photos")[:NEWS_LIMIT])
    summary["forms_to_complete"] = [row for row in form_status_rows_for(people, club) if row["submitted_at"] is None and is_send_open(row["send"], now)][:FORMS_LIMIT]
    return summary
