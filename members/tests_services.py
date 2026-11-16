"""members.services: derived roles, access scoping, fees, onboarding and the audit trail."""

import datetime
import shutil
import tempfile
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from django_tenants.utils import schema_context

from club.models import Club, Domain
from club.services import create_club, grant_login
from controlpanel.tests import make_user
from events.models import Season
from members.models import AuditEntry, ClubMembership, FamilyMembership, FeePayment, Member
from members.services import access, audit, families, groups, memberships, onboarding
from members.services import members as member_services
from members.services.errors import Invalid, NotAllowed
from rosterchief.storage import private_storage
from teams.models import Position, StaffAssignment, Team, TeamMembership

Role = FamilyMembership.FamilyRole
D = Decimal


def aware(y, m, d):
    return timezone.make_aware(datetime.datetime(y, m, d, 12, 0))


class ServiceTestBase(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="rc-private-test-")
        cls._override = override_settings(PRIVATE_MEDIA_ROOT=cls._tmp)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        connection.set_schema_to_public()
        cls._override.disable()
        shutil.rmtree(cls._tmp, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@svc.test", first="Olivia", last="Owner")
        public = Club(schema_name="public", name="Platform", owner=cls.owner)
        public.auto_create_schema = False
        public.save()
        Domain.objects.create(domain="testserver", tenant=public, is_primary=True)
        cls.club = create_club("Service FC", cls.owner)
        today = timezone.localdate()
        with schema_context(cls.club.schema_name):
            cls.season = Season.objects.create(start_date=today - datetime.timedelta(days=100), end_date=today + datetime.timedelta(days=200))
            cls.old_season = Season.objects.create(start_date=today - datetime.timedelta(days=500), end_date=today - datetime.timedelta(days=200))
            cls.team = Team.objects.create(name="U12", short_name="U12")
            cls.other_team = Team.objects.create(name="U14", short_name="U14")
            cls.coach_pos = Position.objects.create(name="Coach", short_name="C", staff_position=True)
            cls.head_pos = Position.objects.create(name="Head coach", short_name="HC", staff_position=True, management_position=True)

    def setUp(self):
        self._ctx = schema_context(self.club.schema_name)
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self.admin = type(self.owner).objects.get(pk=self.owner.pk)

    def person(self, first, last="Test", **kw):
        return Member.objects.create(first_name=first, last_name=last, **kw)

    def login_for(self, member):
        user, _ = grant_login(self.club, member)
        return type(user).objects.get(pk=user.pk)


class RoleTests(ServiceTestBase):
    def test_same_person_is_member_and_guardian(self):
        parent, child = self.person("Pat"), self.person("Kid")
        families.create_family(self.admin, members=[(parent, Role.PARENT), (child, Role.CHILD)])
        memberships.create_membership(self.admin, parent, self.season)
        self.assertEqual(parent.roles, {Member.Role.MEMBER, Member.Role.GUARDIAN})
        self.assertIn(parent, Member.objects.members())
        self.assertIn(parent, Member.objects.guardians())
        self.assertNotIn(child, Member.objects.guardians())
        self.assertNotIn(child, Member.objects.members())

    def test_parent_without_child_is_not_a_guardian(self):
        parent, other = self.person("Solo"), self.person("Other")
        families.create_family(self.admin, members=[(parent, Role.PARENT), (other, Role.OTHER)])
        self.assertNotIn(parent, Member.objects.guardians())
        self.assertEqual(parent.roles, set())

    def test_staff_only(self):
        coach = self.person("Coach")
        StaffAssignment.objects.create(team=self.team, member=coach, season=self.season, position=self.coach_pos)
        self.assertEqual(coach.roles, {Member.Role.STAFF})
        self.assertIn(coach, Member.objects.staff(self.season))
        self.assertNotIn(coach, Member.objects.staff(self.old_season))
        self.assertNotIn(coach, Member.objects.members())


class AccessTests(ServiceTestBase):
    def setUp(self):
        super().setUp()
        self.player = self.person("Player")
        self.other_player = self.person("Elsewhere")
        self.coach = self.person("Coach", email="coach@svc.test")
        self.old_coach = self.person("Old", email="old@svc.test")
        self.parent = self.person("Parent", email="parent@svc.test")
        self.child = self.person("Child")
        self.sibling_of_nobody = self.person("Stranger", email="stranger@svc.test")
        for m in (self.player, self.child, self.other_player):
            memberships.create_membership(self.admin, m, self.season)
        memberships.create_membership(self.admin, self.sibling_of_nobody, self.season)
        TeamMembership.objects.create(team=self.team, member=self.player, season=self.season)
        TeamMembership.objects.create(team=self.other_team, member=self.other_player, season=self.season)
        StaffAssignment.objects.create(team=self.team, member=self.coach, season=self.season, position=self.head_pos)
        StaffAssignment.objects.create(team=self.team, member=self.old_coach, season=self.old_season, position=self.coach_pos)
        families.create_family(self.admin, members=[(self.parent, Role.PARENT), (self.child, Role.CHILD)])
        self.coach_user = self.login_for(self.coach)
        self.old_coach_user = self.login_for(self.old_coach)
        self.parent_user = self.login_for(self.parent)
        self.stranger_user = self.login_for(self.sibling_of_nobody)

    def visible(self, user, **kw):
        return set(access.members_visible_to(user, **kw))

    def test_admin_sees_and_edits_everyone_except_hidden_roles_by_default(self):
        seen = self.visible(self.admin)
        self.assertIn(self.player, seen)
        self.assertIn(self.other_player, seen)
        self.assertNotIn(self.coach, seen)  # staff-only: hidden unless asked
        self.assertNotIn(self.parent, seen)  # guardian-only
        everyone = self.visible(self.admin, include_guardians=True)
        self.assertTrue({self.coach, self.parent, self.old_coach} <= everyone)
        self.assertTrue(access.can_edit_member(self.admin, self.player))

    def test_everyone_sees_everyone_but_only_manage_members_edits(self):
        members = {self.player, self.other_player, self.child, self.sibling_of_nobody}
        for user in (self.coach_user, self.old_coach_user, self.parent_user, self.stranger_user):
            self.assertTrue(members <= self.visible(user))
            self.assertFalse({self.coach, self.parent, self.old_coach} & self.visible(user))  # staff-only and guardian-only people stay out of the default listing
            self.assertTrue({self.coach, self.parent, self.old_coach} <= self.visible(user, include_guardians=True))
            self.assertTrue(access.can_view_member(user, self.other_player))
            self.assertTrue(access.can_view_member(user, self.parent))
            self.assertFalse(access.can_edit_member(user, self.player))  # not even a management position
        with self.assertRaises(NotAllowed):
            member_services.update_member(self.coach_user, self.player, first_name="Hacked")

    def test_nobody_anonymous_or_inactive_sees_anything(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertEqual(self.visible(AnonymousUser()), set())
        self.assertEqual(self.visible(None), set())
        self.coach_user.is_active = False
        self.assertEqual(self.visible(self.coach_user), set())

    def test_the_door(self):
        self.assertTrue(access.can_access_management(self.admin))
        self.assertTrue(access.can_access_management(self.coach_user))  # staff this season
        self.assertFalse(access.can_access_management(self.old_coach_user))  # staff of a finished season only
        self.assertFalse(access.can_access_management(self.parent_user))
        self.assertFalse(access.can_access_management(self.stranger_user))
        self.assertFalse(access.can_access_management(make_user("loner@svc.test")))

    def test_team_and_settings_rights(self):
        self.assertEqual(access.staffed_team_ids(self.coach_user), {self.team.pk})
        self.assertEqual(access.staffed_team_ids(self.old_coach_user), set())
        self.assertTrue(access.can_manage_settings(self.admin))
        self.assertFalse(access.can_manage_settings(self.coach_user))
        self.assertTrue(access.can_manage_team(self.admin, self.other_team))
        if self.head_pos.management_position:
            self.assertTrue(access.can_manage_team(self.coach_user, self.team))
        self.assertFalse(access.can_manage_team(self.coach_user, self.other_team))
        self.assertFalse(access.can_manage_team(self.parent_user, self.team))
        self.assertFalse(access.can_manage_members(make_user("loner2@svc.test")))


class FeeTests(ServiceTestBase):
    def setUp(self):
        super().setUp()
        self.m = self.person("Payer")
        self.today = timezone.localdate()

    def membership(self, fee="100", **kw):
        return memberships.create_membership(self.admin, self.m, self.season, fee_amount=D(fee), **kw)

    def test_status_progression_and_amount_paid_recomputed(self):
        ms = self.membership()
        self.assertEqual(ms.fee_status, "unpaid")
        memberships.record_payment(self.admin, ms, D("40"))
        ms.refresh_from_db()
        self.assertEqual((ms.fee_status, ms.amount_paid), ("partially_paid", D("40.00")))
        self.assertEqual(memberships.remaining_balance(ms), D("60.00"))
        ClubMembership.objects.filter(pk=ms.pk).update(amount_paid=D("999"))  # tampering is healed by the next sync
        p2 = memberships.record_payment(self.admin, ms, D("60"))
        ms.refresh_from_db()
        self.assertEqual((ms.fee_status, ms.amount_paid), ("paid", D("100.00")))
        memberships.delete_payment(self.admin, p2)
        ms.refresh_from_db()
        self.assertEqual((ms.fee_status, ms.amount_paid), ("partially_paid", D("40.00")))

    def test_zero_fee_is_not_paid(self):
        self.assertEqual(self.membership("0").fee_status, "unpaid")

    def test_overpayment_floors_balance_at_zero(self):
        ms = self.membership()
        memberships.record_payment(self.admin, ms, D("120"))
        self.assertEqual(memberships.remaining_balance(ms), D("0.00"))
        self.assertEqual(ms.fee_status, "paid")

    def test_waived_stays_waived_and_rejects_payments(self):
        ms = self.membership()
        memberships.set_fee_waived(self.admin, ms)
        self.assertEqual(ms.fee_status, "waived")
        self.assertEqual(memberships.remaining_balance(ms), D("0.00"))
        memberships.update_membership(self.admin, ms, fee_amount=D("50"))
        self.assertEqual(ms.fee_status, "waived")
        with self.assertRaises(Invalid):
            memberships.record_payment(self.admin, ms, D("10"))
        memberships.set_fee_waived(self.admin, ms, False)
        self.assertEqual(ms.fee_status, "unpaid")

    def test_bad_amounts(self):
        ms = self.membership()
        for bad in (0, "-5", "abc"):
            with self.assertRaises(Invalid):
                memberships.record_payment(self.admin, ms, bad)

    def test_early_discount_when_paid_by_the_deadline(self):
        deadline = self.today - datetime.timedelta(days=30)
        ms = self.membership(early_payment_deadline=deadline, early_payment_discount=D("10"))
        # deadline is past and nothing was paid in time: the full fee is due
        self.assertEqual(memberships.amount_due(ms), D("100.00"))
        on_time = timezone.make_aware(datetime.datetime.combine(deadline, datetime.time(23, 0)))
        memberships.record_payment(self.admin, ms, D("90"), paid_at=on_time)
        ms.refresh_from_db()
        self.assertEqual(ms.fee_status, "paid")
        self.assertEqual(memberships.amount_due(ms), D("90.00"))

    def test_no_discount_when_paid_after_the_deadline(self):
        deadline = self.today - datetime.timedelta(days=30)
        ms = self.membership(early_payment_deadline=deadline, early_payment_discount=D("10"))
        late = timezone.make_aware(datetime.datetime.combine(deadline + datetime.timedelta(days=1), datetime.time(1, 0)))
        memberships.record_payment(self.admin, ms, D("90"), paid_at=late)
        ms.refresh_from_db()
        self.assertEqual(ms.fee_status, "partially_paid")
        self.assertEqual(memberships.remaining_balance(ms), D("10.00"))

    def test_discount_quoted_before_the_deadline_and_mark_paid(self):
        ms = self.membership(early_payment_deadline=self.today + datetime.timedelta(days=5), early_payment_discount=D("10"))
        self.assertEqual(memberships.remaining_balance(ms), D("90.00"))
        payment = memberships.mark_paid(self.admin, ms)
        self.assertEqual(payment.amount, D("90.00"))
        ms.refresh_from_db()
        self.assertEqual(ms.fee_status, "paid")
        with self.assertRaises(Invalid):
            memberships.mark_paid(self.admin, ms)

    def test_derived_fields_cannot_be_set(self):
        ms = self.membership()
        for field in ("amount_paid", "fee_status"):
            with self.assertRaises(Invalid):
                memberships.update_membership(self.admin, ms, **{field: "paid" if field == "fee_status" else D("1")})
        with self.assertRaises(Invalid):
            memberships.update_membership(self.admin, ms, status="active")

    def test_one_membership_per_season(self):
        self.membership()
        with self.assertRaises(Invalid):
            self.membership()


class OnboardingTests(ServiceTestBase):
    def setUp(self):
        super().setUp()
        self.m = self.person("Newbie")
        self.ms = memberships.create_membership(self.admin, self.m, self.season, fee_amount=D("50"))
        self.cert = onboarding.create_requirement(self.admin, name="Medical certificate", requires_document=True, blocked_event_kinds=["game"])
        self.photo = onboarding.create_requirement(self.admin, name="Photo")

    def state(self):
        return {i.requirement.name: i.state for i in onboarding.checklist_for(self.ms)}

    def test_requirement_crud_and_validation(self):
        with self.assertRaises(Invalid):
            onboarding.create_requirement(self.admin, name="medical certificate")
        with self.assertRaises(Invalid):
            onboarding.create_requirement(self.admin, name="X", blocked_event_kinds=["nonsense"])
        onboarding.update_requirement(self.admin, self.photo, is_active=False)
        self.assertEqual([r.name for r in onboarding.open_requirements()], ["Medical certificate"])
        with self.assertRaises(Invalid):
            onboarding.mark_complete(self.admin, self.ms, self.photo)  # inactive

    def test_full_flow(self):
        self.assertEqual(self.state(), {"Medical certificate": "open", "Photo": "open"})
        with self.assertRaises(Invalid):
            onboarding.mark_complete(self.admin, self.ms, self.cert)  # needs a document
        status = onboarding.mark_complete(self.admin, self.ms, self.cert, document=ContentFile(b"pdf", name="cert.pdf"))
        self.assertTrue(status.is_complete and status.document)
        self.assertEqual(status.completed_by, self.admin)
        path = status.document.name
        self.assertTrue(private_storage.exists(path))
        onboarding.bypass(self.admin, self.ms, self.photo, note="has one on file")
        self.assertEqual(self.state(), {"Medical certificate": "complete", "Photo": "bypassed"})
        self.assertEqual(self.ms.open_requirement_count, 0)

        with self.assertRaises(Invalid):  # all resolved but the fee is not
            memberships.activate(self.admin, self.ms)
        memberships.record_payment(self.admin, self.ms, D("50"))
        self.assertEqual(self.ms.status, "pending")  # paying never activates by itself
        memberships.activate(self.admin, self.ms)
        self.ms.refresh_from_db()
        self.assertEqual((self.ms.status, self.ms.activated_at), ("active", timezone.localdate()))

        reopened = onboarding.reopen(self.admin, self.ms, self.cert)
        self.assertFalse(reopened.is_complete or reopened.document)
        self.assertFalse(private_storage.exists(path))  # reopen deletes the document
        self.assertEqual(self.state()["Medical certificate"], "open")
        with self.assertRaises(Invalid):
            onboarding.reopen(self.admin, self.ms, self.cert)

    def test_activation_blocked_by_open_requirement_and_waiver_suffices(self):
        memberships.set_fee_waived(self.admin, self.ms)
        with self.assertRaises(Invalid) as cm:
            memberships.activate(self.admin, self.ms)
        self.assertIn("Medical certificate", cm.exception.errors["requirements"])
        onboarding.bypass(self.admin, self.ms, self.cert)
        onboarding.bypass(self.admin, self.ms, self.photo)
        memberships.activate(self.admin, self.ms)
        self.assertEqual(self.ms.status, "active")
        memberships.cancel(self.admin, self.ms)
        with self.assertRaises(Invalid):
            memberships.activate(self.admin, self.ms)

    def test_non_admin_cannot_resolve(self):
        user = self.login_for(self.person("Plain", email="plain@svc.test"))
        with self.assertRaises(NotAllowed):
            onboarding.bypass(user, self.ms, self.photo)


class MemberAndAuditTests(ServiceTestBase):
    def entries(self, member=None, action=None):
        qs = AuditEntry.objects.all()
        if member is not None:
            qs = qs.filter(member=member)
        return qs.filter(action=action) if action else qs

    def test_create_update_audit_with_actor_and_diff(self):
        m = member_services.create_member(self.admin, self.club, first_name="Ann", last_name="Lee", email="ann@svc.test", date_of_birth=datetime.date(2010, 5, 1))
        created = self.entries(m, "member.created").get()
        self.assertEqual(created.actor, self.admin)
        self.assertEqual(created.changes["date_of_birth"], {"from": None, "to": "2010-05-01"})
        member_services.update_member(self.admin, m, last_name="Leeds", email="ann@svc.test")
        upd = self.entries(m, "member.updated").get()
        self.assertEqual(upd.changes, {"last_name": {"from": "Lee", "to": "Leeds"}})
        self.assertEqual((upd.target_type, upd.target_id, upd.target_label), ("member", str(m.pk), "Ann Leeds"))
        member_services.update_member(self.admin, m, last_name="Leeds")  # no change, no entry
        self.assertEqual(self.entries(m, "member.updated").count(), 1)
        self.assertEqual([e.action for e in audit.history(m)], ["member.updated", "member.created"])

    def test_validation_and_permissions(self):
        m = self.person("Val")
        with self.assertRaises(Invalid):
            member_services.update_member(self.admin, m, email="not-an-email")
        with self.assertRaises(Invalid):
            member_services.update_member(self.admin, m, user=self.admin)
        with self.assertRaises(NotAllowed):
            member_services.create_member(make_user("x@svc.test"), self.club, first_name="A", last_name="B")
        with self.assertRaises(NotAllowed):
            member_services.delete_member(None, m)

    def _png(self, name="x.png"):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (4, 4), "red").save(buf, "PNG")
        return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")

    def test_profile_photo_upload_replace_remove(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        m = self.person("Pic")
        member_services.set_profile_photo(self.admin, m, self._png("evil.php"))
        m.refresh_from_db()
        first = m.profile_photo.name
        self.assertTrue(first.endswith(".png") and "evil" not in first)
        member_services.set_profile_photo(self.admin, m, self._png())
        m.refresh_from_db()
        self.assertNotEqual(m.profile_photo.name, first)
        self.assertFalse(m.profile_photo.storage.exists(first))
        with self.assertRaises(Invalid):
            member_services.set_profile_photo(self.admin, m, SimpleUploadedFile("a.png", b"<svg/>", content_type="image/png"))
        with self.assertRaises(NotAllowed):
            member_services.set_profile_photo(make_user("y@svc.test"), m, self._png())
        member_services.remove_profile_photo(self.admin, m)
        m.refresh_from_db()
        self.assertFalse(m.profile_photo)
        self.assertEqual(self.entries(m).filter(action__startswith="member.photo").count(), 3)

    def test_player_photo_is_separate_and_audited(self):
        m = self.person("Pla")
        member_services.set_photo(self.admin, m, self._png(), "player_photo")
        m.refresh_from_db()
        self.assertTrue(m.player_photo.name.startswith(f"members/{m.pk}/player-"))
        self.assertFalse(m.profile_photo)
        self.assertEqual(self.entries(m, "member.player_photo_changed").count(), 1)
        member_services.remove_photo(self.admin, m, "player_photo")
        m.refresh_from_db()
        self.assertFalse(m.player_photo)
        self.assertEqual(self.entries(m, "member.player_photo_removed").count(), 1)
        with self.assertRaises(ValueError):
            member_services.set_photo(self.admin, m, self._png(), "user")

    def test_grant_login_is_audited(self):
        m = self.person("Gil", email="gil@svc.test")
        _user, created = member_services.grant_login(self.admin, self.club, m)
        self.assertTrue(created)
        entry = self.entries(m, "member.login_granted").get()
        self.assertEqual(entry.changes["user"]["to"], "gil@svc.test")
        with self.assertRaises(Invalid):
            member_services.grant_login(self.admin, self.club, m)  # already has one
        with self.assertRaises(Invalid):
            member_services.grant_login(self.admin, self.club, self.person("NoMail"))

    def test_delete_rules(self):
        plain = self.person("Del")
        member_services.delete_member(self.admin, plain)
        self.assertFalse(Member.objects.filter(pk=plain.pk).exists())
        gone = self.entries(action="member.deleted").get()
        self.assertEqual((gone.member, gone.target_label, gone.actor), (None, "Del Test", self.admin))

        with_login = self.person("Login", email="login@svc.test")
        member_services.grant_login(self.admin, self.club, with_login)
        with self.assertRaises(Invalid):
            member_services.delete_member(self.admin, with_login)
        member_services.delete_member(self.admin, with_login, detach_login=True)
        self.assertTrue(type(self.owner).objects.filter(email="login@svc.test").exists())  # account untouched

        payer = self.person("Payer")
        ms = memberships.create_membership(self.admin, payer, self.season, fee_amount=D("10"))
        memberships.record_payment(self.admin, ms, D("10"))
        with self.assertRaises(Invalid):
            member_services.delete_member(self.admin, payer)

        me = Member.objects.get(user=self.admin)
        with self.assertRaises(Invalid):
            member_services.delete_member(self.admin, me, detach_login=True)

    def test_family_and_group_flows_are_audited(self):
        a, b = self.person("Ma"), self.person("Kid")
        fam = families.create_family(self.admin, name="Tests")
        families.add_member(self.admin, fam, a, Role.PARENT)
        families.add_member(self.admin, fam, b, Role.CHILD)
        with self.assertRaises(Invalid):
            families.add_member(self.admin, fam, a)
        with self.assertRaises(Invalid):
            families.add_member(self.admin, fam, self.person("Z"), "cousin")
        families.change_role(self.admin, fam, a, Role.GUARDIAN)
        self.assertEqual(self.entries(a, "family.role_changed").get().changes, {"role": {"from": "parent", "to": "guardian"}})
        self.assertIn(a, Member.objects.guardians())
        families.remove_member(self.admin, fam, b)
        self.assertNotIn(a, Member.objects.guardians())
        families.delete_family(self.admin, fam)
        self.assertEqual(self.entries(a, "family.deleted").count(), 1)

        g = groups.create_group(self.admin, "Coaches")
        with self.assertRaises(Invalid):
            groups.create_group(self.admin, "coaches")
        groups.add_member(self.admin, g, a)
        with self.assertRaises(Invalid):
            groups.add_member(self.admin, g, a)
        groups.remove_member(self.admin, g, a)
        with self.assertRaises(Invalid):
            groups.remove_member(self.admin, g, a)
        groups.rename_group(self.admin, g, "Trainers")
        groups.delete_group(self.admin, g)
        actions = list(AuditEntry.objects.filter(action__startswith="group.").order_by("created").values_list("action", flat=True))
        self.assertEqual(actions, ["group.created", "group.member_added", "group.member_removed", "group.updated", "group.deleted"])

    def test_payment_audit_carries_fee_status_diff(self):
        m = self.person("Money")
        ms = memberships.create_membership(self.admin, m, self.season, fee_amount=D("20"))
        memberships.record_payment(self.admin, ms, D("20"))
        entry = self.entries(m, "payment.recorded").get()
        self.assertEqual(entry.changes["fee_status"], {"from": "unpaid", "to": "paid"})
        self.assertEqual(entry.changes["amount_paid"], {"from": "0.00", "to": "20.00"})
        self.assertEqual(entry.changes["amount"]["to"], "20.00")
        self.assertEqual(entry.actor, self.admin)
        self.assertEqual(FeePayment.objects.get().recorded_by, self.admin)


class RecipientsTests(ServiceTestBase):
    def family(self):
        from members.models import Family

        family = Family.objects.create(name="Kid family")
        child = Member.objects.create(first_name="Alex", last_name="Kid", email="alex@kid.test")
        mum = Member.objects.create(first_name="Mia", last_name="Kid", email="mia@kid.test")
        dad = Member.objects.create(first_name="Dan", last_name="Kid", email="dan@kid.test")
        for member, role in ((child, Role.CHILD), (mum, Role.PARENT), (dad, Role.GUARDIAN)):
            FamilyMembership.objects.create(family=family, member=member, role=role)
        return child, mum, dad

    def test_a_child_is_reached_through_the_parents_on_their_behalf(self):
        from unittest import mock

        from members.services import recipients

        child, mum, dad = self.family()
        mum_user = self.login_for(mum)
        dad_user = self.login_for(dad)
        with mock.patch("notifications.inbox.notify") as notify:
            told = recipients.deliver(child, self.club, title="New events on your calendar", body="x")
        self.assertEqual({u.pk for u in told}, {mum_user.pk, dad_user.pk})
        self.assertEqual({call.kwargs["title"] for call in notify.call_args_list}, {"For Alex: New events on your calendar"})

        # With their own login the child hears it as themself, the parents on their behalf.
        child_user = self.login_for(child)
        with mock.patch("notifications.inbox.notify") as notify:
            recipients.deliver(child, self.club, title="Hello", body="x")
        titles = {call.args[0].pk: call.kwargs["title"] for call in notify.call_args_list}
        self.assertEqual(titles[child_user.pk], "Hello")
        self.assertEqual(titles[mum_user.pk], "For Alex: Hello")

    def test_adults_are_not_copied_to_anyone(self):
        from unittest import mock

        from members.services import recipients

        child, mum, _dad = self.family()
        self.login_for(child)
        mum_user = self.login_for(mum)
        with mock.patch("notifications.inbox.notify") as notify:
            told = recipients.deliver(mum, self.club, title="Hello", body="x")
        self.assertEqual([u.pk for u in told], [mum_user.pk])
        self.assertEqual(notify.call_count, 1)
