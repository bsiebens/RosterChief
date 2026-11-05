"""Attendance: audience sync (signals), coach answers and check-ins, notifications and statistics."""

import contextlib
import datetime
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import make_user
from events import tasks
from events.models import Attendance, Event, Season
from events.services import attendance as svc
from members.models import AuditEntry, ClubMembership, Group, GroupMembership, Member
from members.services import memberships
from members.services.errors import Invalid, NotAllowed
from notifications.models import InboxNotification, OutboxEntry
from teams.models import Position, StaffAssignment, Team, TeamMembership

S = Attendance.AttendanceStatus
DAY = datetime.timedelta(days=1)


def later(days=1, hours=0):
    return timezone.now() + datetime.timedelta(days=days, hours=hours)


class AttendanceTestBase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@att.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Attendance FC", cls.owner)
        today = timezone.localdate()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.next_season = Season.objects.create(start_date=today + datetime.timedelta(days=201), end_date=today + datetime.timedelta(days=565))
            cls.u12 = Team.objects.create(name="U12", short_name="U12")
            cls.u14 = Team.objects.create(name="U14", short_name="U14")
            cls.head = Position.objects.create(name="Head coach", short_name="HC", staff_position=True, management_position=True)
            cls.group = Group.objects.create(name="Volunteers")
            cls.alice = Member.objects.create(first_name="Alice", last_name="Able", email="alice@att.test")
            cls.bob = Member.objects.create(first_name="Bob", last_name="Baker")  # no login
            cls.cara = Member.objects.create(first_name="Cara", last_name="Cole", email="cara@att.test")
            cls.coach = Member.objects.create(first_name="Cody", last_name="Coach", email="coach@att.test")
            cls.other_coach = Member.objects.create(first_name="Olga", last_name="Other", email="olga@att.test")
            cls.outsider = Member.objects.create(first_name="Otto", last_name="Outsider", email="otto@att.test")
            StaffAssignment.objects.create(team=cls.u12, member=cls.coach, season=cls.season, position=cls.head)
            StaffAssignment.objects.create(team=cls.u14, member=cls.other_coach, season=cls.season, position=cls.head)
        cls.alice_user, _ = grant_login(cls.club, cls.alice)
        cls.cara_user, _ = grant_login(cls.club, cls.cara)
        cls.coach_user, _ = grant_login(cls.club, cls.coach)
        cls.other_coach_user, _ = grant_login(cls.club, cls.other_coach)
        cls.outsider_user, _ = grant_login(cls.club, cls.outsider)

    def setUp(self):
        self._ctx = schema_context(self.club.schema_name)
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self.admin = type(self.owner).objects.get(pk=self.owner.pk)

    # helpers

    @contextlib.contextmanager
    def run_tasks(self):
        """Run the on-commit callbacks and the attendance tasks they queue right here (instead of through Celery)."""
        with (
            mock.patch.object(tasks.sync_event_attendances, "delay", side_effect=tasks.sync_event_attendances),
            mock.patch.object(tasks.sync_club_wide_events, "delay", side_effect=tasks.sync_club_wide_events),
            self.captureOnCommitCallbacks(execute=True),
        ):
            yield

    def event(self, title="Practice", *, start=None, teams=(), groups=(), club_wide=False, kind=Event.EventKind.TRAINING, season="auto", **extra):
        start = start or later(3)
        values = {"title": title, "start": start, "kind": kind, "club_wide": club_wide, **extra}
        if season == "auto":
            values["season"] = self.season
        elif season is not None:
            values["season"] = season
        event = Event.objects.create(**values)
        if teams:
            event.teams.set(teams)
        if groups:
            event.groups.set(groups)
        return event

    def roster(self, member, team=None, season=None):
        return TeamMembership.objects.create(team=team or self.u12, member=member, season=season or self.season)

    def invited(self, event):
        return set(Attendance.objects.filter(event=event).values_list("member_id", flat=True))

    def inbox_for(self, user):
        return InboxNotification.objects.filter(user=user, club=self.club)

    def outbox_for(self, user):
        return [e for e in OutboxEntry.objects.all() if e.payload.get("user_id") == str(user.pk)]


class AudienceTests(AttendanceTestBase):
    def test_event_with_team_invites_the_roster_of_its_season(self):
        self.roster(self.alice)
        self.roster(self.bob, season=self.next_season)
        practice = self.event(teams=[self.u12])
        self.assertEqual(self.invited(practice), {self.alice.pk})
        self.assertEqual(set(svc.effective_members(practice)), {self.alice})
        self.assertEqual(Attendance.objects.get(event=practice).status, S.NO_RESPONSE)

    def test_event_without_season_resolves_by_start_date(self):
        self.roster(self.alice)
        self.roster(self.bob, season=self.next_season)
        practice = self.event(teams=[self.u12], season=None)
        self.assertEqual(svc.resolve_season(practice), self.season)
        self.assertEqual(self.invited(practice), {self.alice.pk})
        next_year = self.event(teams=[self.u12], season=None, start=timezone.make_aware(datetime.datetime.combine(self.next_season.start_date + DAY * 3, datetime.time(18))))
        self.assertEqual(svc.resolve_season(next_year), self.next_season)
        self.assertEqual(self.invited(next_year), {self.bob.pk})

    def test_groups_invited_and_excluded(self):
        self.roster(self.alice)
        GroupMembership.objects.create(group=self.group, member=self.cara)
        e = self.event(teams=[self.u12], groups=[self.group])
        self.assertEqual(self.invited(e), {self.alice.pk, self.cara.pk})
        e.invited_members.add(self.outsider)
        self.assertEqual(self.invited(e), {self.alice.pk, self.cara.pk, self.outsider.pk})
        e.excluded_members.add(self.alice)
        self.assertEqual(self.invited(e), {self.cara.pk, self.outsider.pk})
        e.excluded_members.add(self.outsider)  # exclusion beats an invite
        self.assertEqual(self.invited(e), {self.cara.pk})
        e.excluded_members.clear()
        self.assertEqual(self.invited(e), {self.alice.pk, self.cara.pk, self.outsider.pk})
        e.invited_members.remove(self.outsider)
        self.assertEqual(self.invited(e), {self.alice.pk, self.cara.pk})

    def test_reverse_side_m2m_changes(self):
        self.roster(self.alice)
        e = self.event()
        self.assertEqual(self.invited(e), set())
        self.u12.scheduled_events.add(e)
        self.assertEqual(self.invited(e), {self.alice.pk})
        self.bob.excluded_from_events.add(e)
        self.cara.invited_to_events.add(e)
        self.assertEqual(self.invited(e), {self.alice.pk, self.cara.pk})
        self.cara.invited_to_events.clear()
        self.assertEqual(self.invited(e), {self.alice.pk})
        self.u12.scheduled_events.clear()
        self.assertEqual(self.invited(e), set())

    def test_changing_event_teams(self):
        self.roster(self.alice)
        self.roster(self.bob, team=self.u14)
        e = self.event(teams=[self.u12])
        e.teams.add(self.u14)
        self.assertEqual(self.invited(e), {self.alice.pk, self.bob.pk})
        e.teams.remove(self.u12)
        self.assertEqual(self.invited(e), {self.bob.pk})
        e.teams.set([self.u12])
        self.assertEqual(self.invited(e), {self.alice.pk})
        e.teams.clear()
        self.assertEqual(self.invited(e), set())

    def test_club_wide_invites_active_members_of_the_season(self):
        active = ClubMembership.objects.create(member=self.alice, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        pending = ClubMembership.objects.create(member=self.bob, season=self.season)
        ClubMembership.objects.create(member=self.cara, season=self.next_season, status=ClubMembership.StatusChoices.ACTIVE)
        self.roster(self.outsider)
        with self.run_tasks():
            e = self.event("AGM", club_wide=True, kind=Event.EventKind.MEETING)
        self.assertEqual(self.invited(e), {self.alice.pk})
        pending.status = ClubMembership.StatusChoices.ACTIVE
        with self.run_tasks():
            pending.save()
        self.assertEqual(self.invited(e), {self.alice.pk, self.bob.pk})
        active.status = ClubMembership.StatusChoices.CANCELLED
        with self.run_tasks():
            active.save(update_fields=["status", "modified"])
        self.assertEqual(self.invited(e), {self.bob.pk})
        with self.run_tasks():
            e.invited_members.add(self.outsider)
        self.assertEqual(self.invited(e), {self.bob.pk, self.outsider.pk})
        with self.run_tasks():
            pending.delete()
        self.assertEqual(self.invited(e), {self.outsider.pk})

    def test_club_wide_work_is_queued_after_commit_not_run_inline(self):
        ClubMembership.objects.create(member=self.alice, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        with mock.patch.object(tasks.sync_event_attendances, "delay") as delay, self.captureOnCommitCallbacks(execute=True) as callbacks:
            e = self.event("AGM", club_wide=True)
            self.assertEqual(self.invited(e), set())  # nothing inline
        self.assertTrue(callbacks)
        delay.assert_called_with(str(e.pk))
        self.assertEqual(self.invited(e), set())
        self.assertEqual(tasks.sync_event_attendances(str(e.pk)), "added 1 attendance row(s)")
        self.assertEqual(self.invited(e), {self.alice.pk})

        with mock.patch.object(tasks.sync_club_wide_events, "delay") as delay, self.captureOnCommitCallbacks(execute=True):
            ClubMembership.objects.create(member=self.bob, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        delay.assert_called_once_with()
        self.assertEqual(self.invited(e), {self.alice.pk})
        tasks.sync_club_wide_events()
        self.assertEqual(self.invited(e), {self.alice.pk, self.bob.pk})
        self.assertEqual(tasks.sync_event_attendances("00000000-0000-0000-0000-000000000000"), "event gone")

    def test_past_club_wide_event_queues_nothing(self):
        with mock.patch.object(tasks.sync_event_attendances, "delay") as delay, self.captureOnCommitCallbacks(execute=True):
            self.event("Old AGM", club_wide=True, start=later(-5))
        delay.assert_not_called()

    def test_club_membership_fee_save_without_status_does_not_resync(self):
        m = ClubMembership.objects.create(member=self.alice, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        with self.run_tasks():
            e = self.event("AGM", club_wide=True)
        Attendance.objects.filter(event=e).delete()  # out of sync on purpose
        with mock.patch.object(tasks.sync_club_wide_events, "delay") as delay, self.captureOnCommitCallbacks(execute=True):
            m.save(update_fields=["fee_status", "modified"])
        delay.assert_not_called()
        with self.run_tasks():
            m.save(update_fields=["status", "modified"])
        self.assertEqual(self.invited(e), {self.alice.pk})

    def test_event_save_resyncs_future_only(self):
        self.roster(self.alice)
        e = self.event(teams=[self.u12])
        Attendance.objects.filter(event=e).delete()
        e.title = "Renamed"
        e.save(update_fields=["title", "modified"])  # irrelevant field: left alone
        self.assertEqual(self.invited(e), set())
        e.save()
        self.assertEqual(self.invited(e), {self.alice.pk})

    def test_past_events_are_never_touched(self):
        past = self.event(start=later(-3), teams=[self.u12])
        self.assertEqual(self.invited(past), set())
        Attendance.objects.create(event=past, member=self.cara, status=S.PRESENT)
        self.roster(self.alice)
        past.teams.add(self.u14)
        past.save()
        self.assertEqual(self.invited(past), {self.cara.pk})
        svc.sync_event_attendances(past)
        self.assertEqual(self.invited(past), {self.cara.pk})


class RosterSignalTests(AttendanceTestBase):
    def test_adding_roster_member_invites_to_future_events_and_notifies_once(self):
        past = self.event("Old", start=later(-2), teams=[self.u12])
        e1 = self.event("Practice 1", start=later(2), teams=[self.u12])
        e2 = self.event("Practice 2", start=later(9), teams=[self.u12])
        other = self.event("U14 practice", teams=[self.u14])
        self.roster(self.alice)
        self.assertEqual(self.invited(e1), {self.alice.pk})
        self.assertEqual(self.invited(e2), {self.alice.pk})
        self.assertEqual(self.invited(past), set())
        self.assertEqual(self.invited(other), set())
        notes = list(self.inbox_for(self.alice_user))
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].title, "New events on your calendar")
        self.assertIn("2 new events", notes[0].body)
        self.assertIn("U12", notes[0].body)
        self.assertEqual(notes[0].href, "/calendar")
        self.assertEqual(notes[0].kind, InboxNotification.Kind.INFO)
        outbox = self.outbox_for(self.alice_user)
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0].payload["title"], "New events on your calendar")
        self.assertEqual(outbox[0].payload["category"], "event")
        self.assertEqual(outbox[0].payload["data"]["href"], "/calendar")
        self.assertEqual(outbox[0].payload["club_id"], str(self.club.pk))

    def test_member_without_login_gets_attendance_but_no_notification(self):
        e = self.event(teams=[self.u12])
        before = OutboxEntry.objects.count()
        self.roster(self.bob)
        self.assertEqual(self.invited(e), {self.bob.pk})
        self.assertFalse(InboxNotification.objects.filter(club=self.club).exists())
        self.assertEqual(OutboxEntry.objects.count(), before)

    def test_no_notification_without_upcoming_events_or_for_edits_or_cancelled(self):
        self.event("Cancelled", teams=[self.u12], cancelled=True)
        spot = self.roster(self.alice)
        self.assertFalse(self.inbox_for(self.alice_user).exists())  # only a cancelled event
        self.event(teams=[self.u12])
        spot.jersey_number = 7
        spot.save()
        self.assertFalse(self.inbox_for(self.alice_user).exists())

    def test_roster_for_other_season_does_not_invite(self):
        e = self.event(teams=[self.u12])
        self.roster(self.alice, season=self.next_season)
        self.assertEqual(self.invited(e), set())
        self.assertFalse(self.inbox_for(self.alice_user).exists())

    def test_removing_roster_member_removes_future_rows_keeps_history(self):
        spot = self.roster(self.alice)
        GroupMembership.objects.create(group=self.group, member=self.alice)
        past = self.event(start=later(-2), teams=[self.u12])
        Attendance.objects.create(event=past, member=self.alice, status=S.PRESENT)
        e = self.event(teams=[self.u12])
        both = self.event("Team + group", teams=[self.u12], groups=[self.group])
        Attendance.objects.filter(event=e, member=self.alice).update(status=S.PRESENT)
        spot.delete()
        self.assertEqual(self.invited(e), set())
        self.assertEqual(self.invited(both), {self.alice.pk})  # still invited through the group
        self.assertEqual(Attendance.objects.get(event=past, member=self.alice).status, S.PRESENT)

    def test_team_services_remove_player(self):
        from teams.services import teams as team_services

        spot = self.roster(self.alice)
        e = self.event(teams=[self.u12])
        team_services.remove_player(self.admin, spot)
        self.assertEqual(self.invited(e), set())

    def test_group_membership_add_and_remove(self):
        e = self.event("Volunteer briefing", groups=[self.group], kind=Event.EventKind.MEETING)
        gm = GroupMembership.objects.create(group=self.group, member=self.cara)
        self.assertEqual(self.invited(e), {self.cara.pk})
        self.assertEqual(self.inbox_for(self.cara_user).count(), 1)
        self.assertIn("1 new event has", self.inbox_for(self.cara_user).first().body)
        self.assertEqual(len(self.outbox_for(self.cara_user)), 1)
        gm.delete()
        self.assertEqual(self.invited(e), set())

    def test_groups_service_remove_member(self):
        from members.services import groups

        GroupMembership.objects.create(group=self.group, member=self.cara)
        e = self.event(groups=[self.group])
        groups.remove_member(self.admin, self.group, self.cara)
        self.assertEqual(self.invited(e), set())

    def test_deleting_a_member_on_team_and_group(self):
        self.roster(self.alice)
        GroupMembership.objects.create(group=self.group, member=self.alice)
        ClubMembership.objects.create(member=self.alice, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        e = self.event(teams=[self.u12], groups=[self.group])
        with self.run_tasks():
            wide = self.event(club_wide=True)
        self.assertEqual(self.invited(e), {self.alice.pk})
        self.assertEqual(self.invited(wide), {self.alice.pk})
        pk = self.alice.pk
        with self.run_tasks():
            Member.objects.get(pk=pk).delete()
        self.assertFalse(Attendance.objects.filter(member_id=pk).exists())
        connection.check_constraints()  # nothing dangling

    def test_delete_membership_service_removes_roster_and_attendance(self):
        membership = ClubMembership.objects.create(member=self.alice, season=self.season)
        self.roster(self.alice)
        e = self.event(teams=[self.u12])
        memberships.delete_membership(self.admin, membership)
        self.assertFalse(TeamMembership.objects.filter(member=self.alice).exists())
        self.assertEqual(self.invited(e), set())

    def test_deleting_a_team_or_group_resyncs_its_events(self):
        self.roster(self.alice)
        self.roster(self.bob, team=self.u14)
        GroupMembership.objects.create(group=self.group, member=self.cara)
        e = self.event(teams=[self.u12, self.u14], groups=[self.group])
        self.assertEqual(self.invited(e), {self.alice.pk, self.bob.pk, self.cara.pk})
        Team.objects.get(pk=self.u14.pk).delete()
        self.assertEqual(self.invited(e), {self.alice.pk, self.cara.pk})
        Group.objects.get(pk=self.group.pk).delete()
        self.assertEqual(self.invited(e), {self.alice.pk})

    def test_roster_add_query_count_does_not_grow_with_events(self):
        from django.test.utils import CaptureQueriesContext

        dan = Member.objects.create(first_name="Dan", last_name="Dee")  # no login: no notification queries
        for i in range(2):
            self.event(f"P{i}", start=later(i + 1), teams=[self.u12])
        with CaptureQueriesContext(connection) as few:
            self.roster(self.bob)
        for i in range(12):
            self.event(f"Q{i}", start=later(i + 5), teams=[self.u12])
        with CaptureQueriesContext(connection) as many:
            self.roster(dan)
        self.assertEqual(len(many.captured_queries), len(few.captured_queries))
        self.assertEqual(Attendance.objects.filter(member=dan).count(), 14)


class CoachAnswerTests(AttendanceTestBase):
    def setUp(self):
        super().setUp()
        self.roster(self.alice)
        self.roster(self.bob)
        self.game = self.event("Game", teams=[self.u12], kind=Event.EventKind.GAME)
        self.a = Attendance.objects.get(event=self.game, member=self.alice)
        self.b = Attendance.objects.get(event=self.game, member=self.bob)
        InboxNotification.objects.all().delete()
        OutboxEntry.objects.all().delete()

    def test_coach_sets_answer(self):
        self.a.note = "maybe late"
        self.a.save()
        svc.set_answer_as_coach(self.coach_user, self.a, "present", "Good form")
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, S.PRESENT)
        self.assertTrue(self.a.answered_by_coach)
        self.assertEqual(self.a.coach_set_by, self.coach)
        self.assertIsNotNone(self.a.coach_set_at)
        self.assertEqual(self.a.coach_note, "Good form")
        self.assertEqual(self.a.note, "")
        entry = AuditEntry.objects.get(action="attendance.coach_answer")
        self.assertEqual(entry.member, self.alice)
        self.assertEqual(entry.changes["status"], {"from": "no_response", "to": "present"})
        self.assertFalse(InboxNotification.objects.exists())  # notify=False

    def test_no_response_resets_and_note_handling(self):
        svc.set_answer_as_coach(self.coach_user, self.a, "absent", "injured")
        svc.set_answer_as_coach(self.coach_user, self.a, "no_response", None)
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, S.NO_RESPONSE)
        self.assertFalse(self.a.answered_by_coach)
        self.assertEqual(self.a.coach_note, "injured")  # None leaves it
        svc.set_answer_as_coach(self.coach_user, self.a, "maybe", "")
        self.a.refresh_from_db()
        self.assertEqual(self.a.coach_note, "")
        with self.assertRaises(Invalid):
            svc.set_answer_as_coach(self.coach_user, self.a, "maybe", "x" * 501)

    def test_permissions(self):
        with self.assertRaises(NotAllowed):
            svc.set_answer_as_coach(self.outsider_user, self.a, "present")
        with self.assertRaises(NotAllowed):
            svc.set_answer_as_coach(self.other_coach_user, self.a, "present")
        with self.assertRaises(NotAllowed):
            svc.record_check_in(self.outsider_user, self.a, True)
        svc.set_answer_as_coach(self.admin, self.a, "absent")
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, S.ABSENT)

    def test_invalid_status_and_cancelled(self):
        with self.assertRaises(Invalid):
            svc.set_answer_as_coach(self.coach_user, self.a, "selected")
        Event.objects.filter(pk=self.game.pk).update(cancelled=True)
        self.a.refresh_from_db()
        with self.assertRaises(Invalid):
            svc.set_answer_as_coach(self.coach_user, self.a, "present")
        with self.assertRaises(Invalid):
            svc.record_check_in(self.coach_user, self.a, True)

    def test_deadline_and_start_do_not_apply(self):
        Event.objects.filter(pk=self.game.pk).update(start=later(-1), deadline=later(-2))
        self.a.refresh_from_db()
        svc.set_answer_as_coach(self.coach_user, self.a, "absent")
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, S.ABSENT)

    def test_selected_player_rules(self):
        Attendance.objects.filter(pk=self.a.pk).update(status=S.SELECTED)
        self.a.refresh_from_db()
        for refused in ("maybe", "no_response"):
            with self.assertRaises(Invalid):
                svc.set_answer_as_coach(self.coach_user, self.a, refused)
        svc.set_answer_as_coach(self.coach_user, self.a, "present")
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, S.SELECTED)  # keeps the line-up's verdict
        svc.set_answer_as_coach(self.coach_user, self.a, "absent")
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, S.ABSENT)
        self.assertTrue(self.a.answered_by_coach)

    def test_selected_player_marked_absent_tells_other_managers(self):
        StaffAssignment.objects.create(team=self.u12, member=self.other_coach, season=self.season, position=self.head)
        Attendance.objects.filter(pk=self.a.pk).update(status=S.SELECTED)
        self.a.refresh_from_db()
        svc.set_answer_as_coach(self.coach_user, self.a, "absent", "private")
        told = self.inbox_for(self.other_coach_user).get()
        self.assertEqual(told.title, "Line-up dropout")
        self.assertNotIn("private", told.body)
        self.assertFalse(self.inbox_for(self.coach_user).exists())
        self.assertFalse(self.inbox_for(self.alice_user).exists())  # notify=False

    def test_not_selected_present_keeps_verdict(self):
        Attendance.objects.filter(pk=self.a.pk).update(status=S.NOT_SELECTED)
        self.a.refresh_from_db()
        svc.set_answer_as_coach(self.coach_user, self.a, "present")
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, S.NOT_SELECTED)

    def test_notify_player(self):
        svc.set_answer_as_coach(self.coach_user, self.a, "absent", "secret note", notify=True)
        note = self.inbox_for(self.alice_user).get()
        self.assertEqual(note.title, "Your answer was changed")
        self.assertIn("Game", note.body)
        self.assertIn("Out", note.body)
        self.assertNotIn("secret", note.body)
        self.assertEqual(note.href, f"/calendar/events/{self.game.pk}")
        outbox = self.outbox_for(self.alice_user)
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0].payload["category"], "coach")
        self.assertEqual(outbox[0].payload["title"], "Your answer was changed")
        self.assertEqual(outbox[0].payload["data"]["href"], f"/calendar/events/{self.game.pk}")
        # unchanged answer: nothing new
        svc.set_answer_as_coach(self.coach_user, self.a, "absent", None, notify=True)
        self.assertEqual(self.inbox_for(self.alice_user).count(), 1)

    def test_notify_member_without_login_gets_nothing(self):
        svc.set_answer_as_coach(self.coach_user, self.b, "present", None, notify=True)
        self.assertFalse(InboxNotification.objects.exists())
        self.assertFalse(OutboxEntry.objects.exists())

    def test_check_in(self):
        svc.record_check_in(self.coach_user, self.a, False)
        self.a.refresh_from_db()
        self.assertIs(self.a.showed_up, False)
        entry = AuditEntry.objects.get(action="attendance.check_in")
        self.assertEqual(entry.changes["showed_up"], {"from": None, "to": False})
        svc.record_check_in(self.coach_user, self.a, None)
        self.a.refresh_from_db()
        self.assertIsNone(self.a.showed_up)
        with self.assertRaises(Invalid):
            svc.record_check_in(self.coach_user, self.a, "yes")


class StatisticsTests(AttendanceTestBase):
    def setUp(self):
        super().setUp()
        self.roster(self.alice)
        self.roster(self.bob)
        self.roster(self.cara)
        # four past practices, oldest first: (alice, bob, cara)
        plan = [
            (S.PRESENT, S.ABSENT, S.PRESENT),
            (S.PRESENT, S.PRESENT, S.EXCUSED),
            (S.ABSENT, S.ABSENT, S.PRESENT),
            (S.PRESENT, S.NO_RESPONSE, S.PRESENT),
        ]
        self.past = []
        for i, statuses in enumerate(plan):
            e = self.event(f"Past {i}", start=later(-(10 - i)), teams=[self.u12], season=None if i == 0 else "auto")
            for member, status in zip((self.alice, self.bob, self.cara), statuses, strict=True):
                Attendance.objects.create(event=e, member=member, status=status)
            self.past.append(e)
        self.upcoming = self.event("Next", teams=[self.u12])
        # alice said present on the last one but did not turn up
        Attendance.objects.filter(event=self.past[-1], member=self.alice).update(showed_up=False)
        # another team's past game
        other = self.event("U14 game", start=later(-3), teams=[self.u14], kind=Event.EventKind.GAME)
        Attendance.objects.create(event=other, member=self.outsider, status=S.ABSENT)

    def test_team_and_club_rate(self):
        # u12: present 7 (alice 3, bob 1, cara 3), absent 3; the U14 game adds one absence club-wide
        self.assertEqual(svc.team_attendance_rate(self.u12, self.season), 70)
        self.assertEqual(svc.team_attendance_rate(self.u12), 70)  # current season by default
        self.assertIsNone(svc.team_attendance_rate(self.u12, self.next_season))
        self.assertEqual(svc.club_attendance_rate(), round(100 * 7 / 11))

    def test_rankings(self):
        rankings = svc.player_attendance_rankings(self.u12)
        self.assertEqual([(r["member"], r["rate"], r["responses"]) for r in rankings], [(self.cara, 100, 3), (self.alice, 75, 4), (self.bob, 33, 3)])
        rankings = svc.player_attendance_rankings(self.u12, minimum_responses=4)
        self.assertEqual([r["member"] for r in rankings], [self.alice])  # too few answers for the others

    def test_missed_recent_practices(self):
        self.assertEqual(list(svc.players_who_missed_recent_practices(self.u12)), [self.bob])
        self.assertEqual(list(svc.players_who_missed_recent_practices(self.u12, count=3)), [])  # bob came to the 2nd
        self.assertEqual(list(svc.players_who_missed_recent_practices(self.u12, count=10)), [])  # not enough history

    def test_no_shows_and_counts(self):
        shows = list(svc.team_no_shows(self.u12))
        self.assertEqual([(s.member, s.event) for s in shows], [(self.alice, self.past[-1])])
        counts = svc.member_attendance_counts(self.bob)
        self.assertEqual(counts, {"present": 1, "absent": 2, "no_reply": 1, "no_shows": 0, "excused": 0, "maybe": 0, "selected": 0})
        self.assertEqual(svc.member_attendance_counts(self.alice)["no_shows"], 1)

    def test_sparkline(self):
        bars = svc.member_attendance_sparkline(self.alice)
        self.assertEqual([b["state"] for b in bars], ["present", "present", "absent", "present", "upcoming"])
        self.assertEqual(bars[-1]["event"], self.upcoming)
        self.assertEqual(len(svc.member_attendance_sparkline(self.alice, limit=2)), 2)

    def test_members_attendance_summary(self):
        summary = svc.members_attendance_summary([self.alice, self.bob, self.coach.pk], last=None)
        self.assertEqual(summary[self.alice.pk]["present"], 3)
        self.assertEqual(summary[self.alice.pk]["absent"], 1)
        self.assertEqual(summary[self.alice.pk]["rate"], 75)
        self.assertEqual(summary[self.alice.pk]["no_shows"], 1)
        self.assertEqual(summary[self.bob.pk]["no_response"], 1)
        self.assertEqual(summary[self.bob.pk]["events"], 4)
        self.assertEqual(summary[self.coach.pk]["events"], 0)
        self.assertIsNone(summary[self.coach.pk]["rate"])
        last_two = svc.members_attendance_summary([self.cara], last=2)[self.cara.pk]
        self.assertEqual(last_two["events"], 2)
        self.assertEqual(last_two["present"], 2)
        self.assertEqual(last_two["rate"], 100)
        Event.objects.filter(pk=self.past[-1].pk).update(cancelled=True)
        self.assertEqual(svc.members_attendance_summary([self.cara], last=None)[self.cara.pk]["events"], 3)
        self.assertEqual(svc.members_attendance_summary([self.cara], last=None, include_cancelled=True)[self.cara.pk]["events"], 4)

    def test_no_current_season(self):
        Season.objects.all().update(start_date=datetime.date(2000, 1, 1), end_date=datetime.date(2000, 12, 31))
        self.assertIsNone(svc.team_attendance_rate(self.u12))
        self.assertIsNone(svc.club_attendance_rate())
        self.assertEqual(svc.player_attendance_rankings(self.u12), [])
        self.assertEqual(svc.member_attendance_sparkline(self.alice), [])
        self.assertEqual(svc.members_attendance_summary([self.alice])[self.alice.pk]["events"], 0)
