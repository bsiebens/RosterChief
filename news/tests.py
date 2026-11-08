"""News notifications (audience, language, dedupe, sweep, editors), the public feed and the member feed."""

import datetime
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.utils import timezone
from django_tenants.utils import schema_context

from authorization.services import assign_role, create_role
from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import make_user
from events.models import Season
from members.models import ClubMembership, Family, FamilyMembership, Member
from news import services as svc
from news import tasks
from news.models import News
from news.services import notifications
from notifications.models import InboxNotification
from teams.models import Team, TeamMembership


class NewsClubMixin:
    """A club with a season, two teams, members with and without logins, a family and a publisher."""

    @classmethod
    def setUpClass(cls):
        cls._mfa = mock.patch("authorization.middleware.is_local_development", return_value=True)
        cls._mfa.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()
        cls._mfa.stop()

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@news.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("News FC", cls.owner)
        cls.host = Domain.objects.get(tenant=cls.club, is_primary=True).domain
        today = datetime.date.today()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=30), end_date=today + datetime.timedelta(days=300))
            cls.team = Team.objects.create(name="Under 12", short_name="U12")
            cls.other_team = Team.objects.create(name="Under 14", short_name="U14")

            def member(first, email=None, *, team=None, status=ClubMembership.StatusChoices.ACTIVE, language=""):
                m = Member.objects.create(first_name=first, last_name="Test", email=email or "", language=language)
                if status:
                    ClubMembership.objects.create(member=m, season=cls.season, status=status)
                if team:
                    TeamMembership.objects.create(member=m, team=team, season=cls.season)
                return m

            cls.ann = member("Ann", "ann@news.test", team=cls.team)
            cls.bob = member("Bob", "bob@news.test")
            cls.cara = member("Cara", "cara@news.test", team=cls.team, language="en")
            cls.dan = member("Dan", "dan@news.test", team=cls.team, status=ClubMembership.StatusChoices.LAPSED)
            cls.kid = member("Kid", team=cls.team)
            cls.kid2 = member("Kid2", team=cls.team)
            cls.parent = member("Parent", "parent@news.test", status=None)
            cls.eve = member("Eve", "eve@news.test", status=None)
            family = Family.objects.create(name="Test")
            FamilyMembership.objects.create(family=family, member=cls.parent, role=FamilyMembership.FamilyRole.PARENT)
            for kid in (cls.kid, cls.kid2):
                FamilyMembership.objects.create(family=family, member=kid, role=FamilyMembership.FamilyRole.CHILD)
        for m in (cls.ann, cls.bob, cls.cara, cls.dan, cls.parent, cls.eve):
            setattr(cls, f"{m.first_name.lower()}_user", grant_login(cls.club, m)[0])
        with schema_context(cls.club.schema_name):
            role = create_role("Editors", ["news.publish_newsitems"], slug="editors")
            assign_role(cls.eve_user, role)
        cls.owner = type(cls.owner).objects.get(pk=cls.owner.pk)

    def setUp(self):
        connection.set_tenant(self.club)
        self.addCleanup(connection.set_schema_to_public)

    def news(self, title="Big win", *, teams=(), status=News.Status.PUBLISHED, visibility="both", published_at=None, notified_at=None, author=None, **kw):
        item = News.objects.create(
            title=title, body=kw.pop("body", "Hello **world** this is the body"), status=status, visibility=visibility, notified_at=notified_at, created_by=author, published_at=published_at or timezone.now() - datetime.timedelta(minutes=1), **kw
        )
        item.teams.set(teams)
        return item

    def inbox(self, user):
        return list(InboxNotification.objects.filter(user=user))


class PublishNotificationTests(NewsClubMixin, TestCase):
    def test_club_wide_goes_to_active_members_and_guardians_once(self):
        item = self.news()
        self.assertEqual(notifications.notify_now(item.pk), 4)  # ann, bob, cara and the parent (once, for two kids)
        for user in (self.ann_user, self.bob_user, self.cara_user, self.parent_user):
            self.assertEqual(len(self.inbox(user)), 1, user)
        self.assertEqual(self.inbox(self.dan_user), [])  # lapsed
        self.assertEqual(self.inbox(self.eve_user), [])
        note = self.inbox(self.ann_user)[0]
        self.assertEqual((note.title, note.href), ("Big win", f"/news/{item.pk}"))
        self.assertEqual(note.body, "Hello world this is the body")
        item.refresh_from_db()
        self.assertIsNotNone(item.notified_at)

    def test_team_item_only_reaches_that_roster_and_guardians(self):
        item = self.news(teams=[self.team])
        notifications.notify_now(item.pk)
        self.assertEqual(self.inbox(self.bob_user), [])
        self.assertEqual(self.inbox(self.dan_user), [])
        for user in (self.ann_user, self.cara_user, self.parent_user):
            self.assertEqual(len(self.inbox(user)), 1)
        other = self.news("Elsewhere", teams=[self.other_team])
        notifications.notify_now(other.pk)
        self.assertEqual(len(self.inbox(self.ann_user)), 1)

    def test_excerpt_is_about_fifteen_words(self):
        item = self.news(body=" ".join(f"w{i}" for i in range(40)))
        notifications.notify_now(item.pk)
        body = self.inbox(self.ann_user)[0].body
        self.assertTrue(body.endswith(" …"))
        self.assertEqual(len(body.split()), 16)

    def test_language_per_recipient(self):
        item = self.news(title="Grote winst", body="Nederlandse tekst", title_en="Big win", body_en="English text")
        notifications.notify_now(item.pk)
        self.assertEqual((self.inbox(self.ann_user)[0].title, self.inbox(self.ann_user)[0].body), ("Grote winst", "Nederlandse tekst"))
        self.assertEqual((self.inbox(self.cara_user)[0].title, self.inbox(self.cara_user)[0].body), ("Big win", "English text"))

    def test_external_only_tells_nobody_but_is_marked(self):
        item = self.news(visibility="external")
        self.assertEqual(notifications.notify_now(item.pk), 0)
        self.assertFalse(InboxNotification.objects.exists())
        item.refresh_from_db()
        self.assertIsNotNone(item.notified_at)

    def test_never_twice(self):
        item = self.news()
        notifications.notify_now(item.pk)
        self.assertEqual(notifications.notify_now(item.pk), 0)
        self.assertEqual(len(self.inbox(self.ann_user)), 1)

    def test_failure_releases_the_claim(self):
        item = self.news()
        with mock.patch.object(notifications, "send_publish_notification", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                notifications.notify_now(item.pk)
        item.refresh_from_db()
        self.assertIsNone(item.notified_at)

    def test_dispatch_queues_a_task(self):
        with mock.patch.object(tasks.notify_published, "delay") as delay:
            notifications.dispatch_publish_notification("abc")
        delay.assert_called_once_with("abc")
        with mock.patch.object(tasks.notify_editors_of_pending_review, "delay") as delay:
            notifications.dispatch_notify_editors_of_pending_review("abc")
        delay.assert_called_once_with("abc")
        with mock.patch.object(tasks.notify_published, "delay", side_effect=OSError):
            notifications.dispatch_publish_notification("abc")  # a broker hiccup does not raise

    def test_publish_service_queues_notification_after_commit(self):
        item = self.news(status=News.Status.DRAFT, published_at=None)
        item.published_at = None
        item.save()
        with mock.patch.object(tasks.notify_published, "delay") as delay, self.captureOnCommitCallbacks(execute=True):
            svc.publish(self.owner, item)
        delay.assert_called_once_with(str(item.pk))


class SweepTests(NewsClubMixin, TestCase):
    def test_sweep_notifies_due_items_only(self):
        due = self.news("Due", published_at=timezone.now() - datetime.timedelta(hours=1))
        future = self.news("Future", published_at=timezone.now() + datetime.timedelta(hours=1))
        done = self.news("Done", notified_at=timezone.now())
        draft = self.news("Draft", status=News.Status.DRAFT)
        counts = notifications.notify_due()
        self.assertEqual(counts["items"], 1)
        titles = set(InboxNotification.objects.values_list("title", flat=True))
        self.assertEqual(titles, {"Due"})
        for item in (due, future, done, draft):
            item.refresh_from_db()
        self.assertIsNotNone(due.notified_at)
        self.assertIsNone(future.notified_at)
        self.assertIsNone(draft.notified_at)
        self.assertEqual(notifications.notify_due(), {"items": 0, "told": 0})

    def test_task_runs_across_clubs(self):
        self.news("Due")
        connection.set_schema_to_public()
        result = tasks.notify_due_news()
        self.assertIn("news item(s)", result)
        with schema_context(self.club.schema_name):
            self.assertTrue(InboxNotification.objects.filter(title="Due").exists())

    def test_beat_entry_registered(self):
        from rosterchief.celery import app

        self.assertEqual(app.conf.beat_schedule["news-notify-due-news"]["task"], "news.notify_due_news")
        self.assertIn("news.notify_due_news", app.tasks)


class EditorNotificationTests(NewsClubMixin, TestCase):
    def test_publishers_hear_about_a_pending_item_except_the_author(self):
        item = self.news(status=News.Status.PENDING_REVIEW, author=self.ann, published_at=None)
        self.assertEqual(notifications.notify_editors_of_pending_review(item), 2)  # the Owner and Eve
        for user in (self.owner, self.eve_user):
            (note,) = self.inbox(user)
            self.assertIn("Ann Test", note.body)
            self.assertIn("Big win", note.title)
        self.assertEqual(self.inbox(self.ann_user), [])
        self.assertEqual(self.inbox(self.bob_user), [])

    def test_author_who_is_a_publisher_is_not_told(self):
        item = self.news(status=News.Status.PENDING_REVIEW, author=self.eve, published_at=None)
        notifications.notify_editors_of_pending_review(item)
        self.assertEqual(self.inbox(self.eve_user), [])
        self.assertEqual(len(self.inbox(self.owner)), 1)

    def test_in_app_only_no_email(self):
        item = self.news(status=News.Status.PENDING_REVIEW, author=self.ann, published_at=None)
        with mock.patch("notifications.inbox.notify") as notify:
            notifications.notify_editors_of_pending_review(item)
        for call in notify.call_args_list:
            self.assertFalse(call.kwargs["send_email"])

    def test_task_wrapper(self):
        item = self.news(status=News.Status.PENDING_REVIEW, author=self.ann, published_at=None)
        self.assertEqual(tasks.notify_editors_of_pending_review(str(item.pk)), "told 2 editor(s)")


class FeedTests(NewsClubMixin, TestCase):
    def test_member_feed_and_public_feed(self):
        internal = self.news("Internal", visibility="internal")
        external = self.news("External", visibility="external")
        both = self.news("Both", visibility="both")
        self.news("Scheduled", published_at=timezone.now() + datetime.timedelta(days=1))
        self.news("Draft", status=News.Status.DRAFT)
        self.assertEqual(set(svc.member_news()), {internal, both})
        self.assertEqual(set(svc.public_news()), {external, both})
        self.assertEqual(svc.localized_news(both, "en-GB")[2], "en")
        self.assertEqual(svc.localized_news(both, "nl")[2], "default")
