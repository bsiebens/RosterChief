"""Line-up service (events.services.lineup) and the scheduled-publish task."""

import datetime

from django.db import connection
from django.test import TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import make_user
from events.models import Attendance, Event, Lineup, LineupSelection, Season
from events.services import lineup as svc
from events.tasks import publish_scheduled_lineups
from members.models import AuditEntry, Member
from members.services.errors import Invalid, NotAllowed
from notifications.models import InboxNotification, OutboxEntry
from teams.models import Position, StaffAssignment, Team, TeamMembership

S = Attendance.AttendanceStatus


class LineupTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@lu.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Lineup FC", cls.owner)
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.u16 = Team.objects.create(name="U16", short_name="U16")
            cls.u18 = Team.objects.create(name="U18", short_name="U18")
            cls.forward = Position.objects.create(name="Forward", short_name="F", ordering=2)
            cls.goalie = Position.objects.create(name="Goalie", short_name="G", ordering=1)
            cls.head = Position.objects.create(name="Head coach", short_name="HC", staff_position=True, management_position=True)

            def member(first, email=""):
                return Member.objects.create(first_name=first, last_name="Test", email=email)

            cls.ann = member("Ann", "ann@lu.test")  # login
            cls.bob = member("Bob", "bob@lu.test")  # login
            cls.cid = member("Cid")  # no login
            cls.dan = member("Dan", "dan@lu.test")  # absent
            cls.eve = member("Eve", "eve@lu.test")  # no attendance row
            cls.out = member("Out", "out@lu.test")  # invited, not on the roster
            cls.coach = member("Coach", "coach@lu.test")
            cls.coach2 = member("Second", "coach2@lu.test")
            cls.other_coach = member("Other", "other@lu.test")
            cls.plain = member("Plain", "plain@lu.test")
            TeamMembership.objects.create(team=cls.u16, season=cls.season, member=cls.ann, position=cls.forward, jersey_number=9)
            TeamMembership.objects.create(team=cls.u16, season=cls.season, member=cls.bob, position=cls.goalie, jersey_number=1)
            TeamMembership.objects.create(team=cls.u16, season=cls.season, member=cls.cid, position=cls.forward)
            TeamMembership.objects.create(team=cls.u16, season=cls.season, member=cls.dan)
            TeamMembership.objects.create(team=cls.u16, season=cls.season, member=cls.eve)
            for coach, team in ((cls.coach, cls.u16), (cls.coach2, cls.u16), (cls.other_coach, cls.u18)):
                StaffAssignment.objects.create(team=team, season=cls.season, member=coach, position=cls.head)
        for m in (cls.ann, cls.bob, cls.dan, cls.eve, cls.out, cls.coach, cls.coach2, cls.other_coach, cls.plain):
            grant_login(cls.club, m)
        with schema_context(cls.club.schema_name):
            for m in (cls.ann, cls.bob, cls.dan, cls.eve, cls.out, cls.coach, cls.coach2, cls.other_coach, cls.plain, cls.cid):
                m.refresh_from_db()
        cls.coach_user, cls.coach2_user, cls.other_user, cls.plain_user = cls.coach.user, cls.coach2.user, cls.other_coach.user, cls.plain.user

    def setUp(self):
        ctx = schema_context(self.club.schema_name)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.game = Event.objects.create(kind=Event.EventKind.GAME, title="U16 vs Rivals", start=timezone.now() + datetime.timedelta(days=3))
        self.game.teams.add(self.u16)
        for m, status in ((self.ann, S.PRESENT), (self.bob, S.MAYBE), (self.cid, S.PRESENT), (self.dan, S.ABSENT), (self.out, S.PRESENT)):
            # the attendance-sync signals already gave the roster a NO_RESPONSE row when the team was added
            Attendance.objects.update_or_create(event=self.game, member=m, defaults={"status": status})
        self.lineup = svc.get_or_create_lineup(self.coach_user, self.game, self.u16)

    def status(self, member):
        return Attendance.objects.get(event=self.game, member=member).status

    def inbox_titles(self, member):
        return list(InboxNotification.objects.filter(user=member.user, club=self.club).values_list("title", flat=True)) if member.user_id else []

    def outbox(self, member):
        return [e.payload for e in OutboxEntry.objects.all() if member.user_id and e.payload.get("user_id") == str(member.user_id)]

    # --- creating ---------------------------------------------------------------------------------------------------

    def test_get_or_create_is_idempotent_and_audited(self):
        self.assertEqual(svc.get_or_create_lineup(self.coach_user, self.game), self.lineup)
        self.assertEqual(self.lineup.created_by, self.coach)
        self.assertEqual(AuditEntry.objects.filter(action="lineup.created").count(), 1)

    def test_team_must_play_in_the_event_and_only_games_have_one(self):
        self.lineup.delete()
        with self.assertRaises(Invalid):
            svc.get_or_create_lineup(self.owner, self.game, self.u18)
        training = Event.objects.create(kind=Event.EventKind.TRAINING, title="Practice", start=timezone.now())
        training.teams.add(self.u16)
        with self.assertRaises(Invalid):
            svc.get_or_create_lineup(self.owner, training, self.u16)
        tournament = Event.objects.create(kind=Event.EventKind.TOURNAMENT, title="Cup", start=timezone.now())
        tournament.teams.add(self.u16, self.u18)
        svc.get_or_create_lineup(self.owner, tournament, self.u18)
        with self.assertRaises(Invalid):  # already one for the other team
            svc.get_or_create_lineup(self.owner, tournament, self.u16)

    # --- picking ----------------------------------------------------------------------------------------------------

    def test_selectable_list(self):
        rows = {r["member"].first_name: r for r in svc.selectable_members(self.lineup)}
        self.assertEqual(set(rows), {"Ann", "Bob", "Cid", "Dan", "Eve"})  # the roster, not "Out"
        self.assertEqual((rows["Ann"]["position"], rows["Ann"]["jersey_number"], rows["Ann"]["status"]), ("Forward", 9, S.PRESENT))
        self.assertTrue(rows["Bob"]["available"])  # maybe counts as available
        self.assertFalse(rows["Dan"]["available"])  # absent
        self.assertEqual((rows["Eve"]["status"], rows["Eve"]["available"]), (S.NO_RESPONSE, False))  # no row
        order = [r["member"].first_name for r in svc.selectable_members(self.lineup)]
        self.assertEqual(order[0], "Bob")  # goalie (ordering 1) first
        self.assertFalse(any(r["selected"] for r in rows.values()))

    def test_toggle(self):
        self.assertTrue(svc.toggle_selection(self.coach_user, self.lineup, self.ann))
        self.assertTrue(LineupSelection.objects.filter(lineup=self.lineup, member=self.ann).exists())
        self.assertFalse(svc.toggle_selection(self.coach_user, self.lineup, self.ann))
        self.assertFalse(LineupSelection.objects.exists())
        for unavailable in (self.dan, self.eve, self.out):
            with self.assertRaises(Invalid):
                svc.toggle_selection(self.coach_user, self.lineup, unavailable)
        self.assertEqual(self.status(self.ann), S.PRESENT)  # picking never touches the answers
        self.assertFalse(InboxNotification.objects.exists())

    def test_set_selection_replaces_and_validates(self):
        svc.set_selection(self.coach_user, self.lineup, [self.ann.pk, self.bob.pk])
        svc.set_selection(self.coach_user, self.lineup, [str(self.bob.pk), str(self.cid.pk)])
        self.assertEqual(svc._selected_ids(self.lineup), {self.bob.pk, self.cid.pk})
        with self.assertRaises(Invalid) as caught:
            svc.set_selection(self.coach_user, self.lineup, [self.ann.pk, self.dan.pk, self.out.pk])
        self.assertEqual(set(caught.exception.errors["member_ids"]), {str(self.dan.pk), str(self.out.pk)})
        self.assertEqual(svc._selected_ids(self.lineup), {self.bob.pk, self.cid.pk})  # nothing saved
        svc.set_selection(self.coach_user, self.lineup, [])
        self.assertFalse(LineupSelection.objects.exists())

    def test_a_pick_who_drops_out_can_stay_or_be_removed(self):
        svc.set_selection(self.coach_user, self.lineup, [self.ann.pk])
        Attendance.objects.filter(event=self.game, member=self.ann).update(status=S.ABSENT)
        svc.set_selection(self.coach_user, self.lineup, [self.ann.pk, self.bob.pk])  # keeping is fine
        rows = {r["member"].first_name: r for r in svc.selectable_members(self.lineup)}
        self.assertTrue(rows["Ann"]["selected"] and not rows["Ann"]["available"])
        self.assertFalse(svc.toggle_selection(self.coach_user, self.lineup, self.ann))

    # --- publishing -------------------------------------------------------------------------------------------------

    def test_publish_flips_statuses_and_notifies(self):
        with self.assertRaises(Invalid):  # empty first publish
            svc.publish_lineup(self.coach_user, self.lineup)
        svc.set_selection(self.coach_user, self.lineup, [self.ann.pk, self.cid.pk])
        self.lineup.scheduled_publish_at = timezone.now() + datetime.timedelta(hours=1)
        self.lineup.save()
        result = svc.publish_lineup(self.coach_user, self.lineup)
        self.assertEqual(result, {"selected": {self.ann.pk, self.cid.pk}, "dropped": set()})
        self.lineup.refresh_from_db()
        self.assertIsNotNone(self.lineup.published_at)
        self.assertIsNone(self.lineup.scheduled_publish_at)
        self.assertEqual([self.status(m) for m in (self.ann, self.cid, self.bob, self.out, self.dan)], [S.SELECTED, S.SELECTED, S.NOT_SELECTED, S.NOT_SELECTED, S.ABSENT])
        self.assertEqual(self.inbox_titles(self.ann), ["You're in the line-up for U16 vs Rivals"])
        note = InboxNotification.objects.get(user=self.ann.user)
        self.assertEqual(note.href, f"/calendar/events/{self.game.pk}")
        [push] = self.outbox(self.ann)
        self.assertEqual((push["title"], push["category"], push["data"]["href"]), ("You're in the line-up for U16 vs Rivals", "lineup", f"/calendar/events/{self.game.pk}"))
        # Bob was just "not selected" (never selected before): no message; Cid has no login: nothing at all.
        self.assertEqual((self.inbox_titles(self.bob), self.outbox(self.bob)), ([], []))
        self.assertEqual(InboxNotification.objects.count(), 1)
        self.assertEqual(OutboxEntry.objects.count(), 1)
        self.assertEqual(AuditEntry.objects.filter(action="lineup.published").count(), 1)

    def test_unpublish_returns_to_a_draft_and_withdraws(self):
        Attendance.objects.filter(event=self.game, member=self.bob).update(status=S.MAYBE)  # an answer the publish would overwrite
        members = (self.ann, self.cid, self.bob, self.out, self.dan)
        before = [self.status(m) for m in members]
        svc.set_selection(self.coach_user, self.lineup, [self.ann.pk, self.cid.pk])
        svc.publish_lineup(self.coach_user, self.lineup)
        self.assertEqual(self.status(self.bob), S.NOT_SELECTED)
        InboxNotification.objects.all().delete()
        svc.unpublish_lineup(self.coach_user, self.lineup)
        self.lineup.refresh_from_db()
        self.assertIsNone(self.lineup.published_at)
        self.assertEqual(svc.status(self.lineup), "draft")  # the picks stay
        self.assertEqual([self.status(m) for m in members], before)  # every earlier answer is back, "maybe" included
        self.assertEqual(self.status(self.bob), S.MAYBE)
        self.assertEqual(self.inbox_titles(self.ann), ["The line-up for U16 vs Rivals was withdrawn"])
        self.assertEqual(self.inbox_titles(self.bob), [])  # never told they were out, so nothing now
        self.assertEqual(AuditEntry.objects.filter(action="lineup.unpublished").count(), 1)
        svc.unpublish_lineup(self.coach_user, self.lineup)  # nothing to withdraw: quiet no-op
        self.assertEqual(AuditEntry.objects.filter(action="lineup.unpublished").count(), 1)

    def test_publish_changes_only_tells_the_changed(self):
        svc.set_selection(self.coach_user, self.lineup, [self.ann.pk, self.bob.pk])
        svc.publish_lineup(self.coach_user, self.lineup)
        InboxNotification.objects.all().delete()
        OutboxEntry.objects.all().delete()
        svc.set_selection(self.coach_user, self.lineup, [self.bob.pk, self.cid.pk])
        self.assertEqual(svc.changes_on_publish(self.lineup), ({self.cid.pk}, {self.ann.pk}))
        svc.publish_lineup(self.coach_user, self.lineup)
        self.assertEqual(self.inbox_titles(self.ann), ["You're not in the line-up this time"])
        self.assertEqual(self.outbox(self.ann)[0]["category"], "lineup")
        self.assertEqual((self.inbox_titles(self.bob), self.outbox(self.bob)), ([], []))  # unchanged
        self.assertEqual(InboxNotification.objects.count(), 1)  # Cid (newly in) has no login
        self.assertEqual(OutboxEntry.objects.count(), 1)
        self.assertEqual([self.status(m) for m in (self.ann, self.bob, self.cid)], [S.NOT_SELECTED, S.SELECTED, S.SELECTED])
        # Publishing again with nothing changed tells nobody; an emptied published line-up may be published.
        svc.publish_lineup(self.coach_user, self.lineup)
        self.assertEqual(InboxNotification.objects.count(), 1)
        svc.set_selection(self.coach_user, self.lineup, [])
        svc.publish_lineup(self.coach_user, self.lineup)
        self.assertEqual(self.inbox_titles(self.bob), ["You're not in the line-up this time"])

    # --- scheduling -------------------------------------------------------------------------------------------------

    def test_schedule_and_unschedule(self):
        soon = timezone.now() + datetime.timedelta(hours=2)
        with self.assertRaises(Invalid):  # nothing picked
            svc.schedule_publish(self.coach_user, self.lineup, soon)
        svc.set_selection(self.coach_user, self.lineup, [self.ann.pk])
        with self.assertRaises(Invalid):
            svc.schedule_publish(self.coach_user, self.lineup, timezone.now() - datetime.timedelta(minutes=1))
        svc.schedule_publish(self.coach_user, self.lineup, soon)
        self.assertEqual(Lineup.objects.get(pk=self.lineup.pk).scheduled_publish_at, soon)
        self.assertEqual(svc.status(self.lineup), "scheduled")
        svc.unschedule(self.coach_user, self.lineup)
        self.assertIsNone(Lineup.objects.get(pk=self.lineup.pk).scheduled_publish_at)
        self.assertEqual(svc.status(self.lineup), "draft")
        self.assertEqual(list(AuditEntry.objects.filter(action__startswith="lineup.").exclude(action="lineup.created").values_list("action", flat=True).order_by("created")), ["lineup.scheduled", "lineup.unscheduled"])

    def test_task_publishes_due_lineups(self):
        svc.set_selection(self.coach_user, self.lineup, [self.ann.pk])
        svc.schedule_publish(self.coach_user, self.lineup, timezone.now() + datetime.timedelta(minutes=5))
        connection.set_schema_to_public()
        self.assertEqual(publish_scheduled_lineups(), "published 0 line-up(s)")  # not due yet
        with schema_context(self.club.schema_name):
            Lineup.objects.filter(pk=self.lineup.pk).update(scheduled_publish_at=timezone.now() - datetime.timedelta(minutes=1))
        self.assertEqual(publish_scheduled_lineups(), "published 1 line-up(s)")
        self.assertEqual(publish_scheduled_lineups(), "published 0 line-up(s)")  # once
        connection.set_schema(self.club.schema_name)
        lineup = Lineup.objects.get(pk=self.lineup.pk)
        self.assertIsNotNone(lineup.published_at)
        self.assertIsNone(lineup.scheduled_publish_at)
        self.assertEqual(self.status(self.ann), S.SELECTED)
        self.assertEqual(self.inbox_titles(self.ann), ["You're in the line-up for U16 vs Rivals"])
        self.assertEqual(len(self.outbox(self.ann)), 1)
        self.assertIsNone(AuditEntry.objects.get(action="lineup.published").actor)

    # --- permissions ------------------------------------------------------------------------------------------------

    def test_permissions(self):
        svc.toggle_selection(self.coach2_user, self.lineup, self.ann)  # another manager of the team: fine
        svc.toggle_selection(self.owner, self.lineup, self.bob)  # settings holder: fine
        for refused in (self.other_user, self.plain_user, None):
            with self.subTest(user=refused):
                for call in (
                    lambda u: svc.get_or_create_lineup(u, self.game, self.u16),
                    lambda u: svc.toggle_selection(u, self.lineup, self.cid),
                    lambda u: svc.set_selection(u, self.lineup, []),
                    lambda u: svc.publish_lineup(u, self.lineup),
                    lambda u: svc.schedule_publish(u, self.lineup, timezone.now() + datetime.timedelta(hours=1)),
                    lambda u: svc.unschedule(u, self.lineup),
                ):
                    with self.assertRaises(NotAllowed):
                        call(refused)
        self.assertEqual(svc._selected_ids(self.lineup), {self.ann.pk, self.bob.pk})

    # --- dropouts ---------------------------------------------------------------------------------------------------

    def test_coach_dropout_tells_the_other_managers(self):
        self.game.teams.add(self.u18)
        told = svc.notify_coach_dropout(self.game, self.ann, self.coach)
        self.assertEqual(told, 2)  # coach2 (U16) and other_coach (U18), not the coach who did it, not the player
        self.assertEqual(self.inbox_titles(self.coach2), ["Line-up dropout"])
        self.assertEqual(self.inbox_titles(self.other_coach), ["Line-up dropout"])
        self.assertEqual((self.inbox_titles(self.coach), self.inbox_titles(self.ann)), ([], []))
        note = InboxNotification.objects.get(user=self.coach2.user)
        self.assertIn("Ann Test", note.body)
        self.assertEqual(note.href, f"/calendar/events/{self.game.pk}")
        self.assertEqual(self.outbox(self.coach2)[0]["category"], "coach")
        self.assertEqual(svc.notify_coach_dropout(self.game, self.ann, self.coach_user), 2)  # a User works too

    def test_nothing_is_sent_from_the_public_schema(self):
        connection.set_schema_to_public()
        self.assertEqual(svc._notify_members([self.ann], title="x", body="", kind="info", href="", category="lineup"), [])
