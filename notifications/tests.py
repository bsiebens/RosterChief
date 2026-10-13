from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from club.models import Club, ClubMembership
from features.models import EmailSuppression
from members.models import Family, FamilyMembership, Member
from rosterchief.test_support import make_season

from . import dispatch
from .channels import EmailChannel
from .dispatch import Channel
from .models import Notification
from .services import notify_members, recipient_emails

User = get_user_model()


class RecipientEmailsTests(TestCase):
    """notifications.services.recipient_emails -- the member's own email if
    they hold a login, plus every parent/guardian's, always."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_a_member_with_a_login_gets_their_own_email(self):
        user = User.objects.create_user(email="jamie@example.com", password="pw-secret-123")
        member = Member.objects.create(user=user, first_name="Jamie", last_name="Doe", email="jamie@example.com")

        self.assertEqual(recipient_emails(member, self.club), ["jamie@example.com"])

    def test_a_member_with_no_login_gets_nothing_from_themselves(self):
        # Roster-imported, never given a login -- routine, not an edge case.
        member = Member.objects.create(first_name="Jamie", last_name="Doe", email="jamie@example.com")

        self.assertEqual(recipient_emails(member, self.club), [])

    def test_guardians_are_always_included_even_with_the_childs_own_login(self):
        user = User.objects.create_user(email="jamie@example.com", password="pw-secret-123")
        family = Family.objects.create(club=self.club)
        child = Member.objects.create(user=user, first_name="Jamie", last_name="Doe", email="jamie@example.com")
        parent = Member.objects.create(first_name="Alex", last_name="Doe", email="alex@example.com")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)

        emails = recipient_emails(child, self.club)

        self.assertIn("jamie@example.com", emails)
        self.assertIn("alex@example.com", emails)

    def test_a_child_with_no_login_still_reaches_their_guardian(self):
        family = Family.objects.create(club=self.club)
        child = Member.objects.create(first_name="Jamie", last_name="Doe")
        parent = Member.objects.create(first_name="Alex", last_name="Doe", email="alex@example.com")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)

        self.assertEqual(recipient_emails(child, self.club), ["alex@example.com"])

    def test_duplicate_addresses_are_deduplicated(self):
        user = User.objects.create_user(email="shared@example.com", password="pw-secret-123")
        family = Family.objects.create(club=self.club)
        child = Member.objects.create(user=user, first_name="Jamie", last_name="Doe", email="shared@example.com")
        parent = Member.objects.create(first_name="Alex", last_name="Doe", email="shared@example.com")
        FamilyMembership.objects.create(family=family, member=child, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=family, member=parent, role=FamilyMembership.FamilyRole.PARENT)

        self.assertEqual(recipient_emails(child, self.club), ["shared@example.com"])

    def test_empty_when_nobody_is_reachable(self):
        member = Member.objects.create(first_name="Jamie", last_name="Doe")

        self.assertEqual(recipient_emails(member, self.club), [])


class NotifyMembersTests(TestCase):
    """notifications.services.notify_members -- one Notification per member,
    emailed to whoever recipient_emails() resolves for them."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.season = make_season(cls.club)

    def make_member(self, *, with_login=True, email="jamie@example.com"):
        user = User.objects.create_user(email=email, password="pw-secret-123") if with_login else None
        return Member.objects.create(user=user, first_name="Jamie", last_name="Doe", email=email if with_login else "")

    def test_creates_one_notification_per_member(self):
        members = [self.make_member(email=f"m{i}@example.com") for i in range(3)]

        notifications = notify_members(members, club=self.club, title="News", body="Something happened.")

        self.assertEqual(len(notifications), 3)
        self.assertEqual(Notification.objects.filter(club=self.club).count(), 3)

    def test_emails_the_resolved_addresses(self):
        member = self.make_member(email="jamie@example.com")

        notify_members([member], club=self.club, title="Big win", body="We won 3-0.")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["jamie@example.com"])
        self.assertIn("Big win", mail.outbox[0].subject)
        self.assertIn("We won 3-0.", mail.outbox[0].body)

    def test_email_is_silenced_while_automated_email_is_paused(self):
        # notify_members -> _send_email routes through rosterchief.mail.send_message,
        # the same choke point every automated send goes through -- see
        # features.models.EmailSuppression's own docstring.
        self.addCleanup(cache.clear)
        EmailSuppression.start()
        member = self.make_member(email="jamie@example.com")

        notify_members([member], club=self.club, title="Big win", body="We won 3-0.")

        self.assertEqual(len(mail.outbox), 0)

    def test_the_email_carries_an_html_alternative(self):
        member = self.make_member()

        notify_members([member], club=self.club, title="Big win", body="We won 3-0.")

        [(html_body, mimetype)] = mail.outbox[0].alternatives
        self.assertEqual(mimetype, "text/html")
        self.assertIn("Big win", html_body)

    def test_attachments_are_carried_on_every_recipients_email(self):
        member = self.make_member()

        notify_members([member], club=self.club, title="Big win", body="We won 3-0.", attachments=[("invoice.pdf", b"%PDF-fake", "application/pdf")])

        [attachment] = mail.outbox[0].attachments
        filename, content, mimetype = attachment
        self.assertEqual(filename, "invoice.pdf")
        self.assertEqual(content, b"%PDF-fake")
        self.assertEqual(mimetype, "application/pdf")

    def test_no_attachments_by_default(self):
        member = self.make_member()

        notify_members([member], club=self.club, title="Big win", body="We won 3-0.")

        self.assertEqual(mail.outbox[0].attachments, [])

    def test_sent_at_and_sent_to_emails_are_recorded(self):
        member = self.make_member(email="jamie@example.com")

        [notification] = notify_members([member], club=self.club, title="News", body="Body.")

        notification.refresh_from_db()  # the email channel records on its own copy of the row
        self.assertIsNotNone(notification.sent_at)
        self.assertEqual(notification.sent_to_emails, ["jamie@example.com"])
        self.assertTrue(notification.is_sent)

    def test_a_notification_is_still_created_when_nobody_is_reachable(self):
        member = self.make_member(with_login=False)

        [notification] = notify_members([member], club=self.club, title="News", body="Body.")

        self.assertEqual(len(mail.outbox), 0)
        self.assertIsNone(notification.sent_at)
        self.assertEqual(notification.sent_to_emails, [])
        self.assertFalse(notification.is_sent)

    def test_the_source_is_recorded_as_a_generic_relation(self):
        member = self.make_member()

        [notification] = notify_members([member], club=self.club, title="News", body="Body.", source=self.season)

        self.assertEqual(notification.source, self.season)


class NotificationModelTests(TestCase):
    def test_str_is_member_and_title(self):
        club = Club.objects.create(name="Ajax United", slug="ajax-united")
        member = Member.objects.create(first_name="Jamie", last_name="Doe")
        notification = Notification.objects.create(club=club, member=member, title="Big win", body="Body.")

        self.assertEqual(str(notification), f"{member} — Big win")

    def test_clean_rejects_a_member_with_no_membership_in_this_club(self):
        club = Club.objects.create(name="Ajax United", slug="ajax-united")
        other_club = Club.objects.create(name="Rival FC", slug="rival-fc")
        season = make_season(other_club)
        member = Member.objects.create(first_name="Jamie", last_name="Doe")
        ClubMembership.objects.create(club=other_club, member=member, season=season, status=ClubMembership.StatusChoices.ACTIVE)

        notification = Notification(club=club, member=member, title="News", body="Body.")

        with self.assertRaises(ValidationError):
            notification.clean()


class EmailPreferenceTests(TestCase):
    """User.email_notifications: "not when the app can notify me" skips the email while the
    account has an active native install -- and only then."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.user = User.objects.create_user(email="alex@example.com", password="pw-secret-123")
        cls.parent = Member.objects.create(user=cls.user, first_name="Alex", last_name="Doe", email="alex@example.com")
        cls.child = Member.objects.create(first_name="Jamie", last_name="Doe")
        family = Family.objects.create(club=cls.club)
        FamilyMembership.objects.create(family=family, member=cls.child, role=FamilyMembership.FamilyRole.CHILD)
        FamilyMembership.objects.create(family=family, member=cls.parent, role=FamilyMembership.FamilyRole.PARENT)

    def setUp(self):
        cache.clear()

    def prefer(self, value):
        self.user.email_notifications = value
        self.user.save(update_fields=["email_notifications"])

    def phone(self, **kwargs):
        from appapi.models import PushDevice

        return PushDevice.objects.create(user=self.user, installation_id="phone", platform="ios", push_token="t", **kwargs)

    def emails(self, **kwargs):
        return recipient_emails(self.child, self.club, honor_preferences=True, **kwargs)

    def test_the_default_keeps_emailing(self):
        self.phone()

        self.assertEqual(self.emails(), ["alex@example.com"])

    def test_unless_app_skips_the_email_while_the_app_is_installed(self):
        self.prefer(User.EmailNotifications.UNLESS_APP)
        self.phone()

        self.assertEqual(self.emails(), [])

    def test_unless_app_still_emails_without_an_active_install(self):
        self.prefer(User.EmailNotifications.UNLESS_APP)

        self.assertEqual(self.emails(), ["alex@example.com"])
        self.phone(enabled=False)
        self.assertEqual(self.emails(), ["alex@example.com"])

    def test_a_guardian_without_an_account_is_always_emailed(self):
        family = Family.objects.get(club=self.club)
        other = Member.objects.create(first_name="Sam", last_name="Doe", email="sam@example.com")
        FamilyMembership.objects.create(family=family, member=other, role=FamilyMembership.FamilyRole.PARENT)
        self.prefer(User.EmailNotifications.UNLESS_APP)
        self.phone()

        self.assertEqual(self.emails(), ["sam@example.com"])

    def test_the_plain_lookup_ignores_preferences(self):
        self.prefer(User.EmailNotifications.UNLESS_APP)
        self.phone()

        self.assertEqual(recipient_emails(self.child, self.club), ["alex@example.com"])

    def test_notify_members_skips_the_email_but_keeps_the_inbox_row(self):
        self.prefer(User.EmailNotifications.UNLESS_APP)
        self.phone()

        notify_members([self.child], club=self.club, title="Hi", body="There")

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Notification.objects.filter(member=self.child).count(), 1)

    def test_an_email_with_an_attachment_is_never_skipped(self):
        self.prefer(User.EmailNotifications.UNLESS_APP)
        self.phone()

        notify_members([self.child], club=self.club, title="Invoice", body="Attached", attachments=[("invoice.pdf", b"%PDF", "application/pdf")])

        self.assertEqual(len(mail.outbox), 1)


class NotificationTranslationTests(TestCase):
    """notify_members renders translatable text once per language in settings.LANGUAGES."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.member = Member.objects.create(first_name="Jamie", last_name="Doe")

    def test_callables_are_rendered_per_language_and_english_is_the_stored_text(self):
        from django.utils.translation import get_language

        (notification,) = notify_members([self.member], club=self.club, title=lambda: f"title-{get_language()}", body=lambda: f"body-{get_language()}", send_email=False)

        self.assertEqual((notification.title, notification.body), ("title-en", "body-en"))
        self.assertEqual(notification.translations, {"en": {"title": "title-en", "body": "body-en"}, "nl": {"title": "title-nl", "body": "body-nl"}})
        self.assertEqual(notification.text_for("nl")["title"], "title-nl")

    def test_plain_text_is_the_same_in_every_language(self):
        (notification,) = notify_members([self.member], club=self.club, title="New article", body="Read it", send_email=False)

        self.assertEqual(notification.translations["nl"], {"title": "New article", "body": "Read it"})

    def test_rows_without_translations_fall_back_to_english(self):
        notification = Notification.objects.create(club=self.club, member=self.member, title="Old", body="Row")

        self.assertEqual(notification.text_for("nl"), {"title": "Old", "body": "Row"})


class RecordingChannel(Channel):
    def __init__(self, name, *, fail=False, reached=1):
        self.name = name
        self.fail = fail
        self.reached = reached
        self.batches = []
        self.users = []

    def deliver_notifications(self, notifications, *, email=True):
        if self.fail:
            raise RuntimeError("boom")
        self.batches.append(([notification.pk for notification in notifications], email))

    def deliver_to_user(self, *, club, user, texts, data, url, thread_id=None, category="general"):
        if self.fail:
            raise RuntimeError("boom")
        self.users.append((user, texts("en"), data, url, thread_id))
        return self.reached


class DispatcherTests(TestCase):
    """notifications.dispatch: the channel registry and the fan-out of a notify_members batch."""

    @classmethod
    def setUpTestData(cls):
        cls.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        cls.user = User.objects.create_user(email="jamie@example.com", password="pw-secret-123")
        cls.member = Member.objects.create(user=cls.user, first_name="Jamie", last_name="Doe", email="jamie@example.com")

    def setUp(self):
        saved = list(dispatch._channels)
        self.addCleanup(lambda: dispatch._channels.__setitem__(slice(None), saved))
        dispatch._channels[:] = []

    def test_every_registered_channel_receives_the_batch(self):
        first, second = RecordingChannel("first"), RecordingChannel("second")
        dispatch.register_channel(first)
        dispatch.register_channel(second)

        notifications = notify_members([self.member], club=self.club, title="News", body="Body.")

        ids = [notification.pk for notification in notifications]
        self.assertEqual(first.batches, [(ids, True)])
        self.assertEqual(second.batches, [(ids, True)])

    def test_one_failing_channel_does_not_stop_the_others(self):
        broken, healthy = RecordingChannel("broken", fail=True), RecordingChannel("healthy")
        dispatch.register_channel(broken)
        dispatch.register_channel(healthy)

        with self.assertLogs("notifications.dispatch", level="ERROR"):
            notify_members([self.member], club=self.club, title="News", body="Body.")

        self.assertEqual(len(healthy.batches), 1)

    def test_send_email_false_suppresses_email_but_not_push(self):
        dispatch.register_channel(EmailChannel())
        push = RecordingChannel("push")
        dispatch.register_channel(push)

        notify_members([self.member], club=self.club, title="News", body="Body.", send_email=False)

        self.assertEqual(mail.outbox, [])
        [(_ids, email)] = push.batches
        self.assertFalse(email)

    def test_email_channel_sends_plain_mail(self):
        dispatch.register_channel(EmailChannel())

        notify_members([self.member], club=self.club, title="News", body="Body.")

        self.assertEqual(len(mail.outbox), 1)

    def test_mail_with_attachments_goes_out_inline_exactly_once(self):
        dispatch.register_channel(EmailChannel())
        push = RecordingChannel("push")
        dispatch.register_channel(push)

        notify_members([self.member], club=self.club, title="Invoice", body="Attached.", attachments=[("invoice.pdf", b"%PDF-fake", "application/pdf")])

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(mail.outbox[0].attachments), 1)
        [(_ids, email)] = push.batches  # push still goes; the channels are told not to mail again
        self.assertFalse(email)

    @override_settings(NOTIFICATIONS_DISPATCH_EAGER=False)
    def test_nothing_is_delivered_before_commit_when_not_eager(self):
        dispatch.register_channel(EmailChannel())
        push = RecordingChannel("push")
        dispatch.register_channel(push)

        with patch("notifications.dispatch.run_in_background", side_effect=lambda func, *args, **kwargs: func(*args, **kwargs)) as background:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                notify_members([self.member], club=self.club, title="News", body="Body.")
            self.assertEqual(len(callbacks), 1)
            background.assert_not_called()
            self.assertEqual(mail.outbox, [])
            self.assertEqual(push.batches, [])

            callbacks[0]()

        background.assert_called_once()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(push.batches), 1)

    def test_native_and_web_channels_are_registered_by_their_apps(self):
        from django.apps import apps

        dispatch._channels[:] = []
        for label in ("notifications", "appapi", "mobile"):
            apps.get_app_config(label).ready()

        self.assertEqual(sorted(channel.name for channel in dispatch.channels()), ["email", "native", "web"])

    def test_dispatch_to_user_sums_channels_and_survives_a_failing_channel(self):
        dispatch.register_channel(RecordingChannel("a", reached=2))
        dispatch.register_channel(RecordingChannel("broken", fail=True))
        dispatch.register_channel(RecordingChannel("b", reached=3))

        with self.assertLogs("notifications.dispatch", level="ERROR"):
            result = dispatch.dispatch_to_user(club=self.club, user=self.user, texts=lambda language: ("T", "B"), data={"kind": "x"}, url="/x", thread_id="t")

        self.assertEqual(result.reached, {"a": 2, "broken": 0, "b": 3})
        self.assertEqual(result.total, 5)

    def test_register_channel_is_idempotent_per_name(self):
        dispatch.register_channel(RecordingChannel("same"))
        replacement = RecordingChannel("same")
        dispatch.register_channel(replacement)

        self.assertEqual(dispatch.channels(), [replacement])
