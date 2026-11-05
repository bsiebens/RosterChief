"""Event tasks and claims (events.services.tasks)."""

import datetime

from django.db import connection
from django.test import TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import make_user
from events.models import Attendance, Event, EventTask, EventTaskClaim, Season
from events.services import tasks as svc
from members.models import AuditEntry, Family, FamilyMembership, Member
from members.services.errors import Invalid, NotAllowed
from notifications.models import InboxNotification
from teams.models import Position, StaffAssignment, Team


class EventPeopleMixin:
    """A club with a U16 game: a head coach, an assistant (no management position), the U18 head coach, a parent whose
    child is invited, a stranger with a login, and the owner (settings holder)."""

    prefix = "t"

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()

    @classmethod
    def setUpTestData(cls):
        p = cls.prefix
        cls.owner = make_user(f"owner@{p}.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club(f"{p.upper()} FC", cls.owner)
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.u16 = Team.objects.create(name="U16", short_name="U16")
            cls.u18 = Team.objects.create(name="U18", short_name="U18")
            head = Position.objects.create(name="Head coach", short_name="HC", staff_position=True, management_position=True)
            assistant = Position.objects.create(name="Assistant", short_name="AC", staff_position=True, management_position=False)

            def member(first, last="Test", email=""):
                return Member.objects.create(first_name=first, last_name=last, email=email)

            cls.coach = member("Coach", email=f"coach@{p}.test")
            cls.assistant = member("Assist", email=f"assist@{p}.test")
            cls.other_coach = member("Other", email=f"other@{p}.test")
            cls.parent = member("Pim", "Siebens", email=f"parent@{p}.test")
            cls.child = member("Noor", "Siebens")  # no login
            cls.stranger = member("Sam", "Stranger", email=f"stranger@{p}.test")
            cls.solo = member("Solo", "Player", email=f"solo@{p}.test")
            family = Family.objects.create(name="")
            FamilyMembership.objects.create(family=family, member=cls.parent, role=FamilyMembership.FamilyRole.PARENT)
            FamilyMembership.objects.create(family=family, member=cls.child, role=FamilyMembership.FamilyRole.CHILD)
            StaffAssignment.objects.create(team=cls.u16, season=cls.season, member=cls.coach, position=head)
            StaffAssignment.objects.create(team=cls.u16, season=cls.season, member=cls.assistant, position=assistant)
            StaffAssignment.objects.create(team=cls.u18, season=cls.season, member=cls.other_coach, position=head)
        for m in (cls.coach, cls.assistant, cls.other_coach, cls.parent, cls.stranger, cls.solo):
            grant_login(cls.club, m)
        with schema_context(cls.club.schema_name):
            for m in (cls.coach, cls.assistant, cls.other_coach, cls.parent, cls.stranger, cls.solo, cls.child):
                m.refresh_from_db()

    def setUp(self):
        ctx = schema_context(self.club.schema_name)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.event = Event.objects.create(kind=Event.EventKind.GAME, title="U16 vs Rivals", start=timezone.now() + datetime.timedelta(days=3))
        self.event.teams.add(self.u16)
        for m in (self.child, self.solo):
            Attendance.objects.get_or_create(event=self.event, member=m)

    def inbox(self, member):
        return list(InboxNotification.objects.filter(user=member.user, club=self.club).values_list("title", flat=True))


class TaskPermissionTests(EventPeopleMixin, TestCase):
    prefix = "tperm"

    def test_write_matrix(self):
        allowed = {self.owner: True, self.coach.user: True, self.assistant.user: False, self.other_coach.user: False, self.parent.user: False, self.stranger.user: False}
        for user, expected in allowed.items():
            with self.subTest(user=user.email):
                self.assertEqual(svc.can_manage_tasks(user, self.event), expected)
                if expected:
                    task = svc.create_task(user, self.event, title="Bring fruit")
                    svc.update_task(user, task, title="Bring oranges")
                    svc.delete_task(user, task)
                else:
                    with self.assertRaises(NotAllowed):
                        svc.create_task(user, self.event, title="Bring fruit")
        task = svc.create_task(self.coach.user, self.event, title="Bring fruit")
        for user in (self.assistant.user, self.other_coach.user, self.parent.user):
            with self.assertRaises(NotAllowed):
                svc.update_task(user, task, title="x")
            with self.assertRaises(NotAllowed):
                svc.delete_task(user, task)

    def test_view_matrix(self):
        svc.create_task(self.coach.user, self.event, title="Bring fruit")
        # Management app users (staff of any team, settings holders), the audience (own member or a child invited).
        for user in (self.owner, self.coach.user, self.assistant.user, self.other_coach.user, self.parent.user, self.solo.user):
            with self.subTest(user=user.email):
                self.assertTrue(svc.can_view_event_extras(user, self.event))
                self.assertEqual([t.title for t in svc.list_tasks(user, self.event)], ["Bring fruit"])
        self.assertFalse(svc.can_view_event_extras(self.stranger.user, self.event))
        with self.assertRaises(NotAllowed):
            svc.list_tasks(self.stranger.user, self.event)

    def test_audit_entries(self):
        task = svc.create_task(self.coach.user, self.event, title="Bring fruit", needed_quantity=2)
        svc.update_task(self.coach.user, task, needed_quantity=3)
        svc.update_task(self.coach.user, task, needed_quantity=3)  # no change, no entry
        svc.delete_task(self.coach.user, task)
        entries = list(AuditEntry.objects.order_by("created").values_list("action", "changes"))
        self.assertEqual([a for a, _ in entries], ["event.task_created", "event.task_updated", "event.task_deleted"])
        self.assertEqual(entries[0][1]["title"], {"from": None, "to": "Bring fruit"})
        self.assertEqual(entries[1][1], {"needed_quantity": {"from": 2, "to": 3}})


class TaskValidationTests(EventPeopleMixin, TestCase):
    prefix = "tval"

    def test_rules(self):
        for kwargs, field in (({"title": "  "}, "title"), ({"title": "x" * 256}, "title"), ({"title": "ok", "needed_quantity": 0}, "needed_quantity"), ({"title": "ok", "needed_quantity": "2"}, "needed_quantity")):
            with self.subTest(kwargs=kwargs), self.assertRaises(Invalid) as ctx:
                svc.create_task(self.coach.user, self.event, **kwargs)
            self.assertIn(field, ctx.exception.errors)
        task = svc.create_task(self.coach.user, self.event, title="  Bring fruit ", description=" cut ")
        self.assertEqual((task.title, task.description, task.needed_quantity, task.created_by), ("Bring fruit", "cut", 1, self.coach))

    def test_quantity_cannot_drop_below_claims(self):
        task = svc.create_task(self.coach.user, self.event, title="Drive", needed_quantity=2)
        svc.claim_task(self.coach.user, task, self.solo)
        svc.claim_task(self.coach.user, task, self.parent)
        with self.assertRaises(Invalid):
            svc.update_task(self.coach.user, task, needed_quantity=1)
        svc.update_task(self.coach.user, task, needed_quantity=2, description="Away game")
        task.refresh_from_db()
        self.assertEqual(task.description, "Away game")


class ClaimTests(EventPeopleMixin, TestCase):
    prefix = "tclaim"

    def make_task(self, needed_quantity=1):
        return svc.create_task(self.coach.user, self.event, title="Bring fruit", needed_quantity=needed_quantity)

    def test_member_claims_for_themself(self):
        task = self.make_task()
        claim = svc.claim_task(self.solo.user, task)
        self.assertEqual((claim.task, claim.member), (task, self.solo))
        self.assertTrue(AuditEntry.objects.filter(action="event.task_claimed", member=self.solo).exists())
        self.assertTrue(svc.list_tasks(self.solo.user, self.event)[0].claimed_by_me)

    def test_duplicate_claim_is_refused(self):
        task = self.make_task(needed_quantity=3)
        svc.claim_task(self.solo.user, task)
        with self.assertRaises(Invalid):
            svc.claim_task(self.solo.user, task)
        with self.assertRaises(Invalid):
            svc.claim_task(self.coach.user, task, self.solo)
        self.assertEqual(task.claims.count(), 1)

    def test_capacity(self):
        task = self.make_task(needed_quantity=2)
        svc.claim_task(self.solo.user, task)
        svc.claim_task(self.parent.user, task)
        with self.assertRaises(Invalid) as ctx:
            svc.claim_task(self.coach.user, task)
        self.assertIn("fully covered", str(ctx.exception))
        self.assertEqual(task.claims.count(), 2)
        listed = svc.list_tasks(self.coach.user, self.event)[0]
        self.assertEqual((listed.claimed_count, listed.open_slots), (2, 0))

    def test_claiming_for_someone_else_needs_management_rights(self):
        task = self.make_task(needed_quantity=5)
        for user in (self.assistant.user, self.other_coach.user, self.parent.user):
            with self.subTest(user=user.email), self.assertRaises(NotAllowed):
                svc.claim_task(user, task, self.solo)
        svc.claim_task(self.owner, task, self.solo)  # settings holder without a member record
        svc.claim_task(self.coach.user, task, self.child)  # member without a login: fine, just not notified
        self.assertEqual(task.claims.count(), 2)

    def test_outsider_cannot_claim_for_themself(self):
        task = self.make_task()
        with self.assertRaises(NotAllowed):
            svc.claim_task(self.stranger.user, task)

    def test_unclaim_rules(self):
        task = self.make_task(needed_quantity=3)
        own = svc.claim_task(self.solo.user, task)
        theirs = svc.claim_task(self.parent.user, task)
        with self.assertRaises(NotAllowed):
            svc.unclaim_task(self.solo.user, theirs)
        with self.assertRaises(NotAllowed):
            svc.unclaim_task(self.assistant.user, theirs)
        svc.unclaim_task(self.solo.user, own)
        svc.unclaim_task(self.coach.user, theirs)
        self.assertFalse(task.claims.exists())
        self.assertEqual(AuditEntry.objects.filter(action="event.task_unclaimed").count(), 2)
        self.assertFalse(svc.unclaim_member(self.solo.user, task))  # nothing left: safe to repeat
        svc.claim_task(self.solo.user, task)
        self.assertTrue(svc.unclaim_member(self.solo.user, task))
        with self.assertRaises(NotAllowed):
            svc.unclaim_member(self.parent.user, task, self.solo)

    def test_claim_label(self):
        self.assertEqual(svc.claim_label_for(self.solo), "Solo Player")
        self.assertEqual(svc.claim_label_for(self.parent), "Siebens family")  # unnamed family: its surnames
        self.assertEqual(svc.claim_label_for(self.child), "Siebens family")
        family = Family.objects.create(name="De Smet")
        FamilyMembership.objects.create(family=family, member=self.solo, role=FamilyMembership.FamilyRole.PARENT)
        self.assertEqual(svc.claim_label_for(self.solo), "De Smet family")
        task = self.make_task(needed_quantity=2)
        svc.claim_task(self.parent.user, task)
        svc.claim_task(self.solo.user, task)
        self.assertEqual(svc.list_tasks(self.coach.user, self.event)[0].claim_labels, ["Siebens family", "De Smet family"])


class TaskNotificationTests(EventPeopleMixin, TestCase):
    prefix = "tnote"

    def test_new_task_notifies_the_audience_only_when_asked(self):
        with self.captureOnCommitCallbacks(execute=True):
            svc.create_task(self.coach.user, self.event, title="Quiet")
        self.assertFalse(InboxNotification.objects.exists())
        with self.captureOnCommitCallbacks(execute=True):
            svc.create_task(self.coach.user, self.event, title="Bring fruit", notify=True)
        self.assertEqual(self.inbox(self.solo), ["New task"])
        self.assertEqual(InboxNotification.objects.count(), 1)  # the child has no login, nobody else is invited

    def test_on_behalf_claims_and_removals_notify_the_member(self):
        task = svc.create_task(self.coach.user, self.event, title="Bring fruit")
        with self.captureOnCommitCallbacks(execute=True):
            claim = svc.claim_task(self.coach.user, task, self.solo)
        with self.captureOnCommitCallbacks(execute=True):
            svc.unclaim_task(self.coach.user, claim)
        self.assertEqual(sorted(self.inbox(self.solo)), ["Removed from a task", "You were signed up for a task"])
        self.assertEqual(self.inbox(self.coach), [])

    def test_own_claim_given_back_notifies_the_creator(self):
        task = svc.create_task(self.coach.user, self.event, title="Bring fruit")
        with self.captureOnCommitCallbacks(execute=True):
            claim = svc.claim_task(self.parent.user, task)
        self.assertEqual(InboxNotification.objects.count(), 0)  # claiming for yourself is quiet
        with self.captureOnCommitCallbacks(execute=True):
            svc.unclaim_task(self.parent.user, claim)
        self.assertEqual(self.inbox(self.coach), ["Task slot open again"])
        self.assertIn("Siebens family", InboxNotification.objects.get().body)

    def test_deleting_a_claimed_task_tells_the_claimants(self):
        task = svc.create_task(self.coach.user, self.event, title="Bring fruit", needed_quantity=3)
        svc.claim_task(self.solo.user, task)
        svc.claim_task(self.coach.user, task)
        svc.claim_task(self.coach.user, task, self.child)
        with self.captureOnCommitCallbacks(execute=True):
            svc.delete_task(self.coach.user, task)
        self.assertEqual(self.inbox(self.solo), ["Task removed"])
        self.assertEqual(self.inbox(self.coach), [])  # never the actor
        self.assertFalse(EventTask.objects.exists())
        self.assertFalse(EventTaskClaim.objects.exists())
