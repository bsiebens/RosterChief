"""events.services.events: creating / editing / deleting events and series, scopes, validation and permissions;
events.services.calendar.events_between."""

import datetime
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import make_user
from events.models import Event, EventSeries, EventSubtype, Location, Opponent, Season
from events.services import calendar, subtypes
from events.services import events as svc
from events.services import recurrence as rec
from members.models import AuditEntry, Group, GroupMembership, Member
from members.services.errors import Invalid, NotAllowed
from teams.models import Position, StaffAssignment, Team, TeamMembership


def at(days, hour=19, minute=0):
    """Local aware datetime ``days`` from today at hour:minute."""
    day = timezone.localdate() + datetime.timedelta(days=days)
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))


WEEKLY = {"frequency": "weekly", "interval": 1, "weekdays": ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]}  # daily-ish, any start day matches


class EventServiceTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@ev.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Events FC", cls.owner)
        today = timezone.localdate()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=200), end_date=today + datetime.timedelta(days=300))
            cls.team_a = Team.objects.create(name="Under 12", short_name="U12")
            cls.team_b = Team.objects.create(name="Under 14", short_name="U14")
            cls.group_1 = Group.objects.create(name="Committee")
            cls.group_2 = Group.objects.create(name="Volunteers")
            head = Position.objects.create(name="Head coach", short_name="HC", staff_position=True, management_position=True)
            assistant = Position.objects.create(name="Assistant", short_name="AC", staff_position=True)
            cls.head_m = Member.objects.create(first_name="Head", last_name="Coach", email="head@ev.test")
            cls.assistant_m = Member.objects.create(first_name="Assi", last_name="Stant", email="assi@ev.test")
            cls.group_m = Member.objects.create(first_name="Gro", last_name="Up", email="group@ev.test")
            cls.player = Member.objects.create(first_name="Pla", last_name="Yer", email="player@ev.test")
            StaffAssignment.objects.create(team=cls.team_a, member=cls.head_m, season=cls.season, position=head)
            StaffAssignment.objects.create(team=cls.team_a, member=cls.assistant_m, season=cls.season, position=assistant)
            GroupMembership.objects.create(group=cls.group_1, member=cls.group_m, is_admin=True)
            TeamMembership.objects.create(team=cls.team_a, member=cls.player, season=cls.season)
            cls.away = Location.objects.create(name="Their Rink", address="1 Road", city="Ghent", zip_code="9000", country="BE")
            cls.home = Location.objects.create(name="Our Rink", address="2 Road", city="Mechelen", zip_code="2800", country="BE", is_home=True)
            cls.rivals = Opponent.objects.create(name="Rivals")
        cls.head_u, _ = grant_login(cls.club, cls.head_m)
        cls.assistant_u, _ = grant_login(cls.club, cls.assistant_m)
        cls.group_u, _ = grant_login(cls.club, cls.group_m)

    def setUp(self):
        self._ctx = schema_context(self.club.schema_name)
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        User = type(self.owner)
        self.admin = User.objects.get(pk=self.owner.pk)
        self.head = User.objects.get(pk=self.head_u.pk)
        self.assistant = User.objects.get(pk=self.assistant_u.pk)
        self.grouper = User.objects.get(pk=self.group_u.pk)

    def training(self, actor=None, **kw):
        values = {"kind": "training", "start": at(3), "end": at(3, 20, 30), "team_ids": [self.team_a.pk], **kw}
        return svc.create_event(actor or self.admin, **values)

    def series(self, actor=None, days=1, **kw):
        values = {"title": "Practice", "kind": "training", "start": at(days), "end": at(days, 20, 30), "gathering": at(days, 18, 45), "team_ids": [self.team_a.pk], "recurrence": WEEKLY, **kw}
        return svc.create_series(actor or self.admin, **values)

    def errors(self, fn, *args, **kw):
        with self.assertRaises(Invalid) as cm:
            fn(*args, **kw)
        return cm.exception.errors

    # -- one-off events --

    def test_create_one_off(self):
        e = self.training(deadline=at(2))
        self.assertEqual((e.title, e.season, e.series, e.created_by.user_id), ("Training U12", self.season, None, self.admin.pk))
        self.assertEqual(list(e.teams.all()), [self.team_a])
        entry = AuditEntry.objects.get(action="event.created")
        self.assertEqual(entry.target_id, str(e.pk))
        h = self.training(self.head, title="Goalies")
        self.assertEqual((h.title, h.created_by), ("Goalies", self.head_m))

    def test_game_titles(self):
        g = svc.create_event(self.admin, kind="game", start=at(5), team_ids=[self.team_a.pk], opponent_id=self.rivals.pk, location_id=self.home.pk, title="ignored")
        self.assertEqual(g.title, "U12 vs Rivals")
        self.assertEqual(g.end - g.start, datetime.timedelta(hours=2))
        svc.update_event(self.admin, g, location_id=self.away.pk)
        g.refresh_from_db()
        self.assertEqual(g.title, "Rivals vs U12")
        # no opponent: just our teams (and groups); the friendly flag no longer changes the title
        self.assertEqual(svc.create_event(self.admin, kind="game", start=at(5), team_ids=[self.team_a.pk]).title, "U12")
        self.assertEqual(svc.create_event(self.admin, kind="game", start=at(5), team_ids=[self.team_a.pk], is_friendly=True).title, "U12")
        both = svc.create_event(self.admin, kind="game", start=at(5), team_ids=[self.team_a.pk, self.team_b.pk])
        self.assertEqual(both.title, "U12, U14")
        # the game's subtype template always applies, {teams} being the match-up with the home side first
        dev = subtypes.create_subtype(self.admin, kind="game", name="Development", title_template="Development Game {teams}")
        home = svc.create_event(self.admin, kind="game", subtype_id=dev.pk, start=at(5), team_ids=[self.team_a.pk], opponent_id=self.rivals.pk, location_id=self.home.pk)
        self.assertEqual(home.title, "Development Game U12 vs Rivals")
        away = svc.create_event(self.admin, kind="game", subtype_id=dev.pk, start=at(5), team_ids=[self.team_a.pk], opponent_id=self.rivals.pk, location_id=self.away.pk)
        self.assertEqual(away.title, "Development Game Rivals vs U12")
        self.assertEqual(svc.create_event(self.admin, kind="game", subtype_id=dev.pk, start=at(5), team_ids=[self.team_a.pk]).title, "Development Game U12")

    def test_group_names_are_part_of_the_title(self):
        e = svc.create_event(self.admin, kind="training", start=at(3), team_ids=[self.team_a.pk], group_ids=[self.group_1.pk])
        self.assertEqual(e.title, "Training U12, Committee")

    def test_training_title_comes_from_the_subtype_template(self):
        plain = svc.create_event(self.admin, kind="training", start=at(3), team_ids=[self.team_a.pk, self.team_b.pk])
        self.assertEqual(plain.title, "Training U12, U14")
        fitness = subtypes.create_subtype(self.admin, kind="training", name="Fitness", title_template="Fitness {teams}")
        e = svc.create_event(self.admin, kind="training", subtype_id=fitness.pk, start=at(3), team_ids=[self.team_a.pk])
        self.assertEqual((e.title, e.subtype), ("Fitness U12", fitness))
        self.assertEqual(svc.create_event(self.admin, kind="training", subtype_id=fitness.pk, title="Own", start=at(3), team_ids=[self.team_a.pk]).title, "Own")
        # a game follows its subtype's template
        derby = subtypes.create_subtype(self.admin, kind="game", name="Derby", title_template="Derby {teams}")
        g = svc.create_event(self.admin, kind="game", subtype_id=derby.pk, start=at(5), team_ids=[self.team_a.pk])
        self.assertEqual(g.title, "Derby U12")
        # other kinds always need their own title
        meet = subtypes.create_subtype(self.admin, kind="meeting", name="Board", title_template="Board meeting")
        self.assertEqual(set(self.errors(svc.create_event, self.admin, kind="meeting", subtype_id=meet.pk, start=at(2))), {"title"})

    def test_subtype_must_match_the_kind_and_resets_when_the_kind_changes(self):
        fitness = subtypes.create_subtype(self.admin, kind="training", name="Fitness")
        self.assertEqual(set(self.errors(svc.create_event, self.admin, kind="meeting", title="x", subtype_id=fitness.pk, start=at(2))), {"subtype_id"})
        e = self.training(subtype_id=fitness.pk)
        svc.update_event(self.admin, e, kind="meeting", title="Chat")
        e.refresh_from_db()
        self.assertIsNone(e.subtype)
        fitness.delete()  # deleting a subtype keeps the events
        self.assertTrue(Event.objects.filter(pk=e.pk).exists())

    def test_subtype_carries_over_to_series_occurrences(self):
        fitness = subtypes.create_subtype(self.admin, kind="training", name="Fitness")
        series = self.series(subtype_id=fitness.pk)
        self.assertEqual(series.subtype, fitness)
        self.assertTrue(series.occurrences.exists())
        self.assertFalse(series.occurrences.exclude(subtype=fitness).exists())

    def test_subtype_management_needs_the_settings_permission(self):
        with self.assertRaises(NotAllowed):
            subtypes.create_subtype(self.head, kind="training", name="Nope")
        a = subtypes.create_subtype(self.admin, kind="training", name="Fitness")
        self.assertEqual(set(self.errors(subtypes.create_subtype, self.admin, kind="training", name="fitness")), {"name"})
        self.assertEqual(set(self.errors(subtypes.create_subtype, self.admin, kind="bogus", name="X")), {"kind"})
        self.training(subtype_id=a.pk)
        self.assertEqual(set(self.errors(subtypes.update_subtype, self.admin, a, kind="meeting")), {"kind"})
        subtypes.update_subtype(self.admin, a, name="Strength")
        self.assertTrue(AuditEntry.objects.filter(action="event_subtype.updated").exists())
        with self.assertRaises(NotAllowed):
            subtypes.delete_subtype(self.head, a)
        subtypes.delete_subtype(self.admin, a)
        self.assertFalse(EventSubtype.objects.exists())

    def test_validation(self):
        e = self.errors(svc.create_event, self.admin, kind="meeting")
        self.assertEqual(set(e), {"start", "title"})
        e = self.errors(svc.create_event, self.admin, kind="bogus", title="x", start=at(1), end=at(1, 18), gathering=at(1, 20), deadline=at(2))
        self.assertEqual(set(e), {"kind", "end", "gathering", "deadline"})
        e = self.errors(svc.create_event, self.admin, title="x", start=at(1), club_wide=True, group_ids=[self.group_1.pk])
        self.assertEqual(set(e), {"club_wide"})
        e = self.errors(svc.create_event, self.admin, title="x", start=at(1), team_ids=["nope"], group_ids=[self.team_a.pk], location_id=self.rivals.pk, opponent_id="00000000-0000-0000-0000-000000000000", invited_member_ids=[self.team_a.pk])
        self.assertEqual(set(e), {"team_ids", "group_ids", "location_id", "opponent_id", "invited_member_ids"})
        self.assertEqual(set(self.errors(svc.create_event, self.admin, kind="game", start=at(1))), {"team_ids"})
        ev = self.training()
        self.assertEqual(svc.update_event(self.admin, ev, title="").title, "Training U12")  # a training without a title follows its template
        meeting = svc.create_event(self.admin, kind="meeting", title="Board", start=at(2))
        self.assertEqual(set(self.errors(svc.update_event, self.admin, meeting, title="")), {"title"})  # other kinds keep needing one
        self.assertEqual(set(self.errors(svc.update_event, self.admin, ev, scope="sometimes", title="x")), {"scope"})
        with self.assertRaises(TypeError):
            svc.create_event(self.admin, title="x", start=at(1), score_for=3)

    def test_update_moves_end_with_start(self):
        e = self.training(gathering=at(3, 18, 30))
        svc.update_event(self.admin, e, start=at(4, 10))
        e.refresh_from_db()
        self.assertEqual((e.end, e.gathering), (at(4, 11, 30), at(4, 9, 30)))
        self.assertTrue(AuditEntry.objects.filter(action="event.updated", target_id=str(e.pk)).exists())

    def test_delete_one_off(self):
        e = self.training()
        pk = e.pk
        svc.delete_event(self.admin, e)
        self.assertFalse(Event.objects.filter(pk=pk).exists())
        self.assertEqual(AuditEntry.objects.get(action="event.deleted").target_id, str(pk))

    # -- permissions --

    def test_permission_matrix(self):
        club_wide = {"title": "AGM", "kind": "meeting", "start": at(2), "club_wide": True}
        svc.create_event(self.admin, **club_wide)
        svc.create_event(self.admin, title="B", start=at(2), team_ids=[self.team_b.pk])
        # head coach of A
        self.training(self.head)
        for values in ({"team_ids": [self.team_b.pk]}, {"team_ids": [self.team_a.pk, self.team_b.pk]}, {"team_ids": [], "club_wide": True}, {"team_ids": [], "group_ids": [self.group_1.pk]}, {"team_ids": []}):
            with self.assertRaises(NotAllowed, msg=values):
                self.training(self.head, title="x", **values)
        # group member: own group only
        svc.create_event(self.grouper, title="Meeting", start=at(2), group_ids=[self.group_1.pk])
        with self.assertRaises(NotAllowed):
            svc.create_event(self.grouper, title="Meeting", start=at(2), group_ids=[self.group_2.pk])
        with self.assertRaises(NotAllowed):
            svc.create_event(self.grouper, title="Meeting", start=at(2), team_ids=[self.team_a.pk])
        # assistant coach (no management position): nothing, not even with garbage input
        for kw in ({"team_ids": [self.team_a.pk]}, {"start": None}):
            with self.assertRaises(NotAllowed):
                self.training(self.assistant, **kw)
        with self.assertRaises(NotAllowed):
            self.series(self.assistant)

    def test_edit_permissions(self):
        mine = self.training(self.head)
        theirs = svc.create_event(self.admin, title="B", start=at(2), team_ids=[self.team_b.pk])
        svc.update_event(self.head, mine, title="Renamed")
        with self.assertRaises(NotAllowed):
            svc.update_event(self.head, mine, team_ids=[self.team_b.pk])  # new audience not allowed
        with self.assertRaises(NotAllowed):
            svc.update_event(self.head, theirs, team_ids=[self.team_a.pk])  # existing one not allowed
        with self.assertRaises(NotAllowed):
            svc.delete_event(self.head, theirs)
        with self.assertRaises(NotAllowed):
            svc.update_event(self.assistant, mine, title="x")
        svc.update_event(self.admin, theirs, team_ids=[self.team_a.pk, self.team_b.pk])
        svc.delete_event(self.head, mine)

    # -- series --

    def test_create_series_generates_to_horizon(self):
        s = self.series(self.head)
        self.assertEqual(s.rrule, "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,TU,WE,TH,FR,SA,SU")
        occ = list(s.occurrences.order_by("start"))
        self.assertIn(len(occ), (rec.HORIZON_DAYS - 1, rec.HORIZON_DAYS))  # daily from tomorrow 19:00 up to now + 90 days
        self.assertLessEqual(occ[-1].start, rec.horizon())
        self.assertEqual((occ[0].title, occ[0].end - occ[0].start, occ[0].start - occ[0].gathering), ("Practice", datetime.timedelta(minutes=90), datetime.timedelta(minutes=15)))
        self.assertEqual(list(occ[5].teams.all()), [self.team_a])
        self.assertEqual(svc.series_values(s)["recurrence"]["weekdays"], WEEKLY["weekdays"])
        self.assertTrue(AuditEntry.objects.filter(action="series.created").exists())

    def test_series_recurrence_input(self):
        s = self.series(recurrence={"frequency": "daily", "interval": 2, "count": 3})
        self.assertEqual((s.rrule, s.occurrences.count()), ("FREQ=DAILY;INTERVAL=2;COUNT=3", 3))
        s = self.series(recurrence={"frequency": "weekly", "interval": 1, "weekdays": ["MO"], "until": timezone.localdate() + datetime.timedelta(days=21)})
        self.assertLessEqual(s.occurrences.count(), 3)
        self.assertEqual(rec.recurrence_values(s)["until"], timezone.localdate() + datetime.timedelta(days=21))
        s = self.series(recurrence=None, rrule="RRULE:FREQ=MONTHLY;BYMONTHDAY=1,15")
        self.assertEqual(s.rrule, "FREQ=MONTHLY;BYMONTHDAY=1,15")
        self.assertFalse(rec.recurrence_values(s)["friendly"])
        self.assertEqual(set(self.errors(self.series, recurrence={"frequency": "weekly", "interval": 0})), {"interval", "weekdays"})
        self.assertEqual(set(self.errors(self.series, recurrence={"frequency": "daily", "count": 2, "until": timezone.localdate()})), {"until"})
        self.assertEqual(set(self.errors(self.series, recurrence={"frequency": "daily", "until": timezone.localdate() - datetime.timedelta(days=9)})), {"until"})
        self.assertEqual(set(self.errors(self.series, recurrence=None)), {"frequency"})
        self.assertEqual(set(self.errors(self.series, recurrence=None, rrule="FREQ=SOMETIMES")), {"rrule"})

    def test_update_this_detaches(self):
        s = self.series()
        first, second, third = s.occurrences.order_by("start")[:3]
        svc.update_event(self.admin, second, scope="this", title="Special", start=second.start + datetime.timedelta(hours=1))
        second.refresh_from_db()
        s.refresh_from_db()
        self.assertTrue(second.detached)
        self.assertEqual(second.end - second.start, datetime.timedelta(minutes=90))
        self.assertIn(rec.iso(at(2)), s.excluded_dates)  # the original slot
        svc.update_event(self.admin, third, scope="all", title="Renamed")
        self.assertEqual(Event.objects.get(pk=second.pk).title, "Special")
        self.assertEqual(Event.objects.get(pk=first.pk).title, "Renamed")
        self.assertEqual(s.occurrences.filter(title="Renamed").count(), s.occurrences.count() - 1)
        rec.generate_occurrences(s, until=rec.horizon() + datetime.timedelta(days=1))
        self.assertEqual(s.occurrences.filter(start__date=timezone.localdate() + datetime.timedelta(days=2)).count(), 1)

    def test_update_all_changes_template_and_time(self):
        self.enterContext(patch.object(timezone, "now", return_value=at(0, 12)))
        s = self.series(days=-7)
        past = s.occurrences.filter(start__lt=timezone.now()).count()
        self.assertEqual(past, 7)
        target = s.occurrences.filter(start__gte=timezone.now()).order_by("start")[2]
        ids = set(s.occurrences.values_list("pk", flat=True))
        svc.update_event(self.admin, target, scope="all", title="Evening", start=target.start + datetime.timedelta(hours=1), end=target.start + datetime.timedelta(hours=3), team_ids=[self.team_a.pk, self.team_b.pk])
        s.refresh_from_db()
        self.assertEqual((s.title, s.duration, timezone.localtime(s.dtstart).time()), ("Evening", datetime.timedelta(hours=2), datetime.time(20, 0)))
        self.assertEqual(set(s.occurrences.values_list("pk", flat=True)), ids)  # moved in place, nothing recreated
        future = s.occurrences.filter(start__gte=timezone.now())
        self.assertTrue(all(timezone.localtime(e.start).time() == datetime.time(20, 0) and e.title == "Evening" and e.teams.count() == 2 for e in future))
        self.assertEqual(s.occurrences.filter(start__lt=timezone.now(), title="Practice").count(), 7)  # past untouched
        e = self.errors(svc.update_event, self.admin, target, scope="all", start=target.start + datetime.timedelta(days=1))
        self.assertEqual(set(e), {"start"})
        self.assertEqual(set(self.errors(svc.update_event, self.admin, target, scope="all", cancelled=True)), {"cancelled"})
        self.assertTrue(AuditEntry.objects.filter(action="series.updated", target_id=str(s.pk)).exists())

    def test_update_following_splits(self):
        s = self.series()
        occ = list(s.occurrences.order_by("start"))
        out = svc.update_event(self.admin, occ[10], scope="following", title="Later")
        s.refresh_from_db()
        self.assertEqual(s.occurrences.count(), 10)
        self.assertTrue(all(e.title == "Practice" for e in s.occurrences.all()))
        self.assertLess(s.until, occ[10].start)
        new = out.series
        self.assertNotEqual(new.pk, s.pk)
        self.assertEqual(new.occurrences.count(), len(occ) - 10)
        self.assertTrue(all(e.title == "Later" for e in new.occurrences.all()))
        self.assertEqual(rec.generate_occurrences(s), [])
        # following from the first occurrence = the whole series
        svc.update_event(self.admin, occ[0], scope="following", title="Whole")
        self.assertEqual(EventSeries.objects.count(), 2)
        self.assertTrue(all(e.title == "Whole" for e in s.occurrences.all()))

    def test_delete_scopes(self):
        self.enterContext(patch.object(timezone, "now", return_value=at(0, 12)))
        s = self.series(days=-3)
        series_pk = s.pk
        occ = list(s.occurrences.order_by("start"))
        # this: EXDATE + row gone, never regenerated
        svc.delete_event(self.admin, occ[5], scope="this")
        s.refresh_from_db()
        self.assertEqual(s.excluded_dates, [rec.iso(occ[5].start)])
        rec.regenerate_series(s)
        self.assertFalse(Event.objects.filter(start=occ[5].start).exists())
        # following
        svc.delete_event(self.admin, occ[20], scope="following")
        s.refresh_from_db()
        self.assertEqual(s.occurrences.count(), 19)
        self.assertEqual(rec.generate_occurrences(s, until=rec.horizon() + datetime.timedelta(days=30)), [])
        # all: past occurrences stay as one-off events
        svc.delete_event(self.admin, occ[1], scope="all")
        self.assertFalse(EventSeries.objects.filter(pk=series_pk).exists())
        kept = Event.objects.filter(pk__in=[o.pk for o in occ])
        self.assertEqual(kept.count(), 3)
        self.assertTrue(all(e.series_id is None and e.start < timezone.now() for e in kept))
        self.assertTrue(AuditEntry.objects.filter(action="series.deleted", target_id=str(series_pk)).exists())

    def test_series_permissions(self):
        s = self.series(self.head)
        occ = s.occurrences.order_by("start")[3]
        svc.update_event(self.head, occ, scope="all", title="Mine")
        with self.assertRaises(NotAllowed):
            svc.update_event(self.head, occ, scope="all", team_ids=[self.team_b.pk])
        with self.assertRaises(NotAllowed):
            svc.delete_event(self.assistant, occ, scope="all")
        other = self.series(team_ids=[self.team_b.pk])
        with self.assertRaises(NotAllowed):
            svc.update_series(self.head, other, title="x")
        with self.assertRaises(NotAllowed):
            svc.delete_series(self.head, other)
        svc.delete_series(self.head, s)

    def test_update_series_recurrence_regenerates(self):
        s = self.series(days=-14, recurrence={"frequency": "weekly", "interval": 1, "weekdays": ["MO"]})
        past = set(s.occurrences.filter(start__lt=timezone.now()).values_list("pk", flat=True))
        detached = s.occurrences.filter(start__gte=timezone.now()).order_by("start").first()
        svc.update_event(self.admin, detached, scope="this", title="Kept")
        svc.update_series(self.admin, s, recurrence={"frequency": "weekly", "interval": 1, "weekdays": ["TU"]})
        s.refresh_from_db()
        self.assertEqual(s.rrule, "FREQ=WEEKLY;INTERVAL=1;BYDAY=TU")
        future = s.occurrences.filter(start__gte=timezone.now(), detached=False)
        self.assertTrue(future.exists())
        self.assertTrue(all(timezone.localtime(e.start).weekday() == 1 for e in future))
        self.assertTrue(past <= set(s.occurrences.values_list("pk", flat=True)))
        self.assertEqual(Event.objects.get(pk=detached.pk).title, "Kept")
        svc.update_series(self.admin, s, title="New name")
        self.assertTrue(all(e.title == "New name" for e in s.occurrences.filter(start__gte=timezone.now(), detached=False)))
        stopped = svc.stop_series(self.admin, s)
        self.assertLessEqual(stopped.until, timezone.now())
        n = s.occurrences.count()
        self.assertEqual(rec.generate_occurrences(s, until=rec.horizon() + datetime.timedelta(days=60)), [])
        self.assertEqual(s.occurrences.count(), n)

    # -- calendar reads --

    def test_events_between(self):
        a = self.training()
        b = svc.create_event(self.admin, title="B", kind="meeting", start=at(3, 10), team_ids=[self.team_b.pk], location_id=self.home.pk)
        agm = svc.create_event(self.admin, title="AGM", kind="meeting", start=at(10), club_wide=True, excluded_member_ids=[self.head_m.pk])
        g = svc.create_event(self.admin, title="Board", start=at(3, 12), group_ids=[self.group_1.pk], invited_member_ids=[self.player.pk])
        svc.update_event(self.admin, g, cancelled=True)
        rng = (at(3, 0), at(4, 0))
        self.assertEqual(list(calendar.events_between(*rng)), [b, g, a])
        self.assertEqual(list(calendar.events_between(at(3, 20), at(3, 23))), [a])  # overlaps by its end
        self.assertTrue(list(calendar.events_between(*rng))[1].cancelled)
        self.assertEqual(list(calendar.events_between(*rng, team_id=self.team_a.pk)), [a])
        self.assertEqual(list(calendar.events_between(*rng, location_id=self.home.pk, kind="meeting")), [b])
        self.assertEqual(list(calendar.events_between(*rng, group_id=self.group_1.pk)), [g])
        wide = (at(0), at(20))
        self.assertEqual(list(calendar.events_between(*wide, mine_member=self.player)), [g, a, agm])
        self.assertEqual(list(calendar.events_between(*wide, mine_member=self.head_m)), [a])  # staff of A; excluded from the AGM
        self.assertEqual(list(calendar.events_between(*wide, mine_member=self.group_m)), [g, agm])
        with self.assertNumQueries(6):  # 3 queries, each preceded by the tenant's SET search_path
            rows = list(calendar.events_between(*wide))
            [(e.location, e.opponent, e.series, list(e.teams.all()), list(e.groups.all())) for e in rows]
