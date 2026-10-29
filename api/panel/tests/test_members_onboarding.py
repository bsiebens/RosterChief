import datetime
import io
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from waffle import get_waffle_flag_model

from api.panel.tests.test_members import MembersPanelTestCase
from api.protected.tests import CLUB_HOST
from club.models import Club, ClubMembership, ClubRole, MemberRequirementStatus, OnboardingRequirement, Season
from club.services.onboarding import current_membership_for, mark_complete
from events.models import Event
from members.models import Member
from teams.models import NumberPool, OfficialLevel, OfficialProfile, Position, RefereeLevel, RefereeProfile, StaffAssignment, Team, TeamMembership


def png(name="p.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class OnboardingTestCase(MembersPanelTestCase):
    def send_multipart(self, path, token, data=None):
        return self.client.post(f"/api/v1{path}", data=data or {}, HTTP_HOST=CLUB_HOST, HTTP_AUTHORIZATION=f"Bearer {token}")

    def requirement(self, club=None, **kwargs):
        defaults = {"name": "Medical certificate", "requires_document": True}
        return OnboardingRequirement.objects.create(club=club or self.club, **{**defaults, **kwargs})

    def pending_member(self, first_name="Pia", fee_status="paid", status="pending"):
        member = Member.objects.create(first_name=first_name, last_name="Pending")
        membership = ClubMembership.objects.create(club=self.club, member=member, season=self.season, status=status, fee_status=fee_status, signed_up_at=datetime.date.today())
        return member, membership

    def other_club(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        season = Season.objects.create(club=other, start_date=datetime.date.today() - datetime.timedelta(days=10), end_date=datetime.date.today() + datetime.timedelta(days=200))
        stranger = Member.objects.create(first_name="Sam", last_name="Stranger")
        membership = ClubMembership.objects.create(club=other, member=stranger, season=season, status="pending")
        return other, stranger, membership


class AccessTests(OnboardingTestCase):
    def test_no_token_is_401(self):
        for method, path in (
            ("get", "/manage/onboarding-requirements"),
            ("post", "/manage/onboarding-requirements"),
            ("get", f"/manage/members/{self.child.pk}/requirements"),
            ("get", "/manage/signup"),
            ("post", "/manage/signup/approve-all-clean"),
            ("put", f"/manage/members/{self.child.pk}/referee-eligibility"),
            ("post", f"/manage/members/{self.child.pk}/photo"),
        ):
            self.assertError(self.api(method, path, host=CLUB_HOST), 401, "unauthenticated")

    def test_plain_member_is_403(self):
        token = self.login()
        req = self.requirement()
        for method, path in (
            ("get", "/manage/onboarding-requirements"),
            ("get", f"/manage/onboarding-requirements/{req.pk}"),
            ("get", f"/manage/members/{self.child.pk}/requirements"),
            ("get", "/manage/signup"),
            ("post", "/manage/signup/approve-all-clean"),
            ("get", f"/manage/members/{self.child.pk}/referee-eligibility"),
            ("get", f"/manage/members/{self.child.pk}/requirements/{req.pk}/document"),
        ):
            self.assertError(self.api(method, path, token=token, host=CLUB_HOST), 403, "forbidden")

    def test_coach_reads_but_cannot_write(self):
        team = Team.objects.create(club=self.club, name="U12")
        position = Position.objects.create(club=self.club, name="Assistant", short_name="AC", staff_position=True)
        StaffAssignment.objects.create(team=team, member=self.member, season=self.season, position=position)
        TeamMembership.objects.create(team=team, member=self.child, season=self.season)
        token = self.login()
        req = self.requirement()
        base = f"/manage/members/{self.child.pk}"
        self.assertEqual(self.api("get", f"{base}/requirements", token=token, host=CLUB_HOST).status_code, 200)
        self.assertEqual(self.api("get", f"{base}/referee-eligibility", token=token, host=CLUB_HOST).status_code, 200)
        for method, path, data in (
            ("get", "/manage/onboarding-requirements", None),
            ("post", "/manage/onboarding-requirements", {"name": "X"}),
            ("post", f"{base}/requirements/{req.pk}/incomplete", None),
            ("post", f"{base}/requirements/{req.pk}/bypass", {"note": "n"}),
            ("put", f"{base}/referee-eligibility", {}),
            ("delete", f"{base}/photo", None),
            ("get", "/manage/signup", None),
            ("post", f"/manage/signup/{self.child.pk}/approve", None),
        ):
            self.assertError(self.api(method, path, token=token, host=CLUB_HOST, data=data), 403, "forbidden")
        self.assertError(self.send_multipart(f"{base}/requirements/{req.pk}/complete", token), 403, "forbidden")
        self.assertError(self.send_multipart(f"{base}/photo", token, {"photo": png()}), 403, "forbidden")

    def test_member_admin_may_write(self):
        token = self.as_role(ClubRole.Roles.MEMBER_ADMIN)
        self.assertEqual(self.api("get", "/manage/signup", token=token, host=CLUB_HOST).status_code, 200)
        self.assertEqual(self.api("post", "/manage/onboarding-requirements", token=token, host=CLUB_HOST, data={"name": "Photo"}).status_code, 201)

    def test_other_clubs_objects_are_404(self):
        other, stranger, _membership = self.other_club()
        foreign_req = self.requirement(club=other, name="Foreign")
        own_req = self.requirement()
        token = self.admin()
        for method, path, data in (
            ("get", f"/manage/onboarding-requirements/{foreign_req.pk}", None),
            ("patch", f"/manage/onboarding-requirements/{foreign_req.pk}", {"name": "x"}),
            ("delete", f"/manage/onboarding-requirements/{foreign_req.pk}", None),
            ("get", f"/manage/members/{stranger.pk}/requirements", None),
            ("post", f"/manage/members/{stranger.pk}/requirements/{own_req.pk}/incomplete", None),
            ("post", f"/manage/members/{self.child.pk}/requirements/{foreign_req.pk}/incomplete", None),
            ("get", f"/manage/members/{stranger.pk}/requirements/{own_req.pk}/document", None),
            ("get", f"/manage/members/{stranger.pk}/referee-eligibility", None),
            ("put", f"/manage/members/{stranger.pk}/referee-eligibility", {}),
            ("delete", f"/manage/members/{stranger.pk}/photo", None),
            ("post", f"/manage/signup/{stranger.pk}/approve", None),
            ("post", f"/manage/signup/{stranger.pk}/cancel", None),
            ("post", f"/manage/signup/{stranger.pk}/link", {"target_member_id": str(self.child.pk)}),
            ("post", f"/manage/signup/{stranger.pk}/place-in-team", {"team_id": str(Team.objects.create(club=self.club, name="T").pk)}),
        ):
            self.assertError(self.api(method, path, token=token, host=CLUB_HOST, data=data), 404, "not_found")
        self.assertTrue(OnboardingRequirement.objects.filter(pk=foreign_req.pk).exists())


class RequirementSettingsTests(OnboardingTestCase):
    def test_crud(self):
        token = self.admin()
        created = self.api("post", "/manage/onboarding-requirements", token=token, host=CLUB_HOST, data={"name": "Photo", "requires_document": True, "blocked_event_kinds": ["game"]})
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        self.assertEqual((body["name"], body["requires_document"], body["blocked_event_kinds"], body["is_active"]), ("Photo", True, ["game"], True))
        url = f"/manage/onboarding-requirements/{body['id']}"
        self.assertEqual([r["name"] for r in self.api("get", "/manage/onboarding-requirements", token=token, host=CLUB_HOST).json()["items"]], ["Photo"])
        patched = self.api("patch", url, token=token, host=CLUB_HOST, data={"is_active": False, "description": "Passport photo"})
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual((patched.json()["is_active"], patched.json()["description"], patched.json()["requires_document"], patched.json()["blocked_event_kinds"]), (False, "Passport photo", True, ["game"]))
        self.assertEqual(self.api("get", url, token=token, host=CLUB_HOST).json()["name"], "Photo")
        self.assertEqual(self.api("delete", url, token=token, host=CLUB_HOST).status_code, 204)
        self.assertFalse(OnboardingRequirement.objects.exists())

    def test_validation(self):
        token = self.admin()
        for data, field in (({"name": ""}, "name"), ({"name": "X", "blocked_event_kinds": ["nope"]}, "blocked_event_kinds")):
            response = self.api("post", "/manage/onboarding-requirements", token=token, host=CLUB_HOST, data=data)
            self.assertError(response, 422, "validation_error")
            self.assertIn(field, response.json()["error"]["fields"])

    def test_name_is_unique_per_club(self):
        token = self.admin()
        self.requirement(name="Photo")
        response = self.api("post", "/manage/onboarding-requirements", token=token, host=CLUB_HOST, data={"name": "Photo"})
        self.assertError(response, 422, "validation_error")
        self.assertIn("name", response.json()["error"]["fields"])
        other, _stranger, _m = self.other_club()
        self.requirement(club=other, name="Shared name")
        self.assertEqual(self.api("post", "/manage/onboarding-requirements", token=token, host=CLUB_HOST, data={"name": "Shared name"}).status_code, 201)

    def test_patch_keeps_own_name_but_rejects_taken_one(self):
        token = self.admin()
        first, second = self.requirement(name="A"), self.requirement(name="B")
        self.assertEqual(self.api("patch", f"/manage/onboarding-requirements/{first.pk}", token=token, host=CLUB_HOST, data={"name": "A", "description": "d"}).status_code, 200)
        self.assertError(self.api("patch", f"/manage/onboarding-requirements/{first.pk}", token=token, host=CLUB_HOST, data={"name": "B"}), 422, "validation_error")
        self.assertEqual(second.name, "B")

    def test_idempotent_free_create_does_not_leak_other_club(self):
        other, _stranger, _m = self.other_club()
        self.requirement(club=other, name="Foreign")
        token = self.admin()
        self.assertEqual(self.api("get", "/manage/onboarding-requirements", token=token, host=CLUB_HOST).json()["items"], [])


class ChecklistTests(OnboardingTestCase):
    def base(self, req):
        return f"/manage/members/{self.child.pk}/requirements/{req.pk}"

    def test_checklist_lists_active_requirements(self):
        req = self.requirement()
        self.requirement(name="Inactive", is_active=False)
        token = self.admin()
        body = self.api("get", f"/manage/members/{self.child.pk}/requirements", token=token, host=CLUB_HOST).json()
        self.assertEqual([i["requirement"]["name"] for i in body["items"]], [req.name])
        self.assertEqual(body["items"][0]["state"], "open")

    def test_member_without_current_membership_is_404(self):
        loner = Member.objects.create(first_name="No", last_name="Season")
        ClubMembership.objects.create(club=self.club, member=loner, season=Season.objects.create(club=self.club, start_date=datetime.date(2000, 1, 1), end_date=datetime.date(2000, 12, 1)), status="active")
        token = self.admin()
        self.assertError(self.api("get", f"/manage/members/{loner.pk}/requirements", token=token, host=CLUB_HOST), 404, "not_found")

    def test_complete_with_document_download_and_reopen(self):
        req = self.requirement()
        token = self.admin()
        document = SimpleUploadedFile("cert.pdf", b"%PDF-1.4 certificate", content_type="application/pdf")
        response = self.send_multipart(self.base(req) + "/complete", token, {"document": document, "note": "Seen on paper"})
        self.assertEqual(response.status_code, 200, response.content)
        item = response.json()
        self.assertEqual((item["state"], item["has_document"], item["note"], item["completed_by"]), ("complete", True, "Seen on paper", self.user.email))
        download = self.api("get", self.base(req) + "/document", token=token, host=CLUB_HOST)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b"".join(download.streaming_content), b"%PDF-1.4 certificate")
        self.assertIn("attachment", download["Content-Disposition"])

        reopened = self.api("post", self.base(req) + "/incomplete", token=token, host=CLUB_HOST)
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual((reopened.json()["state"], reopened.json()["has_document"]), ("open", False))
        self.assertError(self.api("get", self.base(req) + "/document", token=token, host=CLUB_HOST), 404, "not_found")

    def test_complete_without_document(self):
        req = self.requirement(requires_document=False)
        token = self.admin()
        response = self.send_multipart(self.base(req) + "/complete", token)
        self.assertEqual(response.json()["state"], "complete")
        self.assertTrue(MemberRequirementStatus.objects.get().is_complete)

    def test_document_validation(self):
        req = self.requirement()
        token = self.admin()
        bad_type = SimpleUploadedFile("x.exe", b"MZ", content_type="application/octet-stream")
        self.assertError(self.send_multipart(self.base(req) + "/complete", token, {"document": bad_type}), 415, "unsupported_media_type")
        empty = SimpleUploadedFile("x.pdf", b"", content_type="application/pdf")
        self.assertError(self.send_multipart(self.base(req) + "/complete", token, {"document": empty}), 422, "validation_error")
        self.assertFalse(MemberRequirementStatus.objects.exists())

    def test_bypass_needs_a_note_and_replaces_completion(self):
        req = self.requirement()
        token = self.admin()
        self.send_multipart(self.base(req) + "/complete", token, {"document": SimpleUploadedFile("c.pdf", b"%PDF", content_type="application/pdf")})
        response = self.api("post", self.base(req) + "/bypass", token=token, host=CLUB_HOST, data={"note": " "})
        self.assertError(response, 422, "validation_error")
        self.assertIn("note", response.json()["error"]["fields"])
        response = self.api("post", self.base(req) + "/bypass", token=token, host=CLUB_HOST, data={"note": "Has one on file"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["state"], response.json()["has_document"], response.json()["note"]), ("bypassed", False, "Has one on file"))

    def test_document_visible_to_coach_of_member_only(self):
        req = self.requirement()
        player, _membership = self.pending_member("Dana")
        mark_complete(current_membership_for(self.club, player.pk), req, user=self.user, document=SimpleUploadedFile("c.pdf", b"%PDF", content_type="application/pdf"))
        team = Team.objects.create(club=self.club, name="U12")
        position = Position.objects.create(club=self.club, name="Assistant", short_name="AC", staff_position=True)
        StaffAssignment.objects.create(team=team, member=self.member, season=self.season, position=position)
        token = self.login()
        url = f"/manage/members/{player.pk}/requirements/{req.pk}/document"
        self.assertError(self.api("get", url, token=token, host=CLUB_HOST), 404, "not_found")
        TeamMembership.objects.create(team=team, member=player, season=self.season)
        self.assertEqual(self.api("get", url, token=token, host=CLUB_HOST).status_code, 200)


class SignupTests(OnboardingTestCase):
    def test_queue_lists_pending_first_with_checklist(self):
        self.requirement(requires_document=False, blocked_event_kinds=["game"])
        member, _membership = self.pending_member()
        token = self.admin()
        body = self.api("get", "/manage/signup", token=token, host=CLUB_HOST).json()
        self.assertEqual(body["season_id"], str(self.season.pk))
        self.assertTrue(body["requirements_configured"])
        ids = [row["member_id"] for row in body["items"]]
        self.assertEqual(ids[0], str(member.pk))
        self.assertIn(str(self.child.pk), ids)
        row = body["items"][0]
        self.assertEqual((row["status"], row["onboarding_open"], row["is_clean"], row["is_oldest_pending"]), ("pending", 1, False, True))
        self.assertEqual(row["blocked_event_kinds"], ["game"])
        self.assertEqual(row["checklist"][0]["state"], "open")

    def test_clean_active_member_is_not_in_the_queue(self):
        token = self.admin()
        ClubMembership.objects.filter(member=self.child).update(fee_status="paid")
        ids = [row["member_id"] for row in self.api("get", "/manage/signup", token=token, host=CLUB_HOST).json()["items"]]
        self.assertNotIn(str(self.child.pk), ids)

    def test_queue_does_not_leak_other_club(self):
        self.other_club()
        token = self.admin()
        names = {row["first_name"] for row in self.api("get", "/manage/signup", token=token, host=CLUB_HOST).json()["items"]}
        self.assertNotIn("Sam", names)

    def test_queue_shows_possible_duplicates(self):
        member, _m = self.pending_member("Kim")
        Member.objects.filter(pk=member.pk).update(last_name="Parent", date_of_birth=self.child.date_of_birth)
        token = self.admin()
        row = next(r for r in self.api("get", "/manage/signup", token=token, host=CLUB_HOST).json()["items"] if r["member_id"] == str(member.pk))
        self.assertEqual([d["member_id"] for d in row["possible_duplicates"]], [str(self.child.pk)])

    def test_approve_one(self):
        member, membership = self.pending_member()
        token = self.admin()
        response = self.api("post", f"/manage/signup/{member.pk}/approve", token=token, host=CLUB_HOST)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "active")
        membership.refresh_from_db()
        self.assertEqual(membership.status, "active")
        self.assertIsNotNone(membership.activated_at)

    def test_approve_not_ready_is_409(self):
        member, membership = self.pending_member(fee_status="unpaid")
        token = self.admin()
        self.assertError(self.api("post", f"/manage/signup/{member.pk}/approve", token=token, host=CLUB_HOST), 409, "not_ready")
        membership.refresh_from_db()
        self.assertEqual(membership.status, "pending")

    def test_approve_all_clean(self):
        _ready, ready_membership = self.pending_member("Ready")
        _unpaid, unpaid_membership = self.pending_member("Unpaid", fee_status="unpaid")
        token = self.admin()
        response = self.api("post", "/manage/signup/approve-all-clean", token=token, host=CLUB_HOST)
        self.assertEqual(response.json(), {"activated": 1})
        ready_membership.refresh_from_db()
        unpaid_membership.refresh_from_db()
        self.assertEqual((ready_membership.status, unpaid_membership.status), ("active", "pending"))
        self.assertEqual(self.api("post", "/manage/signup/approve-all-clean", token=token, host=CLUB_HOST).json(), {"activated": 0})

    def test_approve_all_clean_without_season_is_409(self):
        past = datetime.date.today() - datetime.timedelta(days=500)
        Season.objects.filter(pk=self.season.pk).update(start_date=past, end_date=past + datetime.timedelta(days=10))
        token = self.admin()
        self.assertError(self.api("post", "/manage/signup/approve-all-clean", token=token, host=CLUB_HOST), 409, "no_active_season")

    def test_open_requirement_blocks_approval_until_resolved(self):
        req = self.requirement(requires_document=False)
        member, _m = self.pending_member()
        token = self.admin()
        self.assertError(self.api("post", f"/manage/signup/{member.pk}/approve", token=token, host=CLUB_HOST), 409, "not_ready")
        self.api("post", f"/manage/members/{member.pk}/requirements/{req.pk}/bypass", token=token, host=CLUB_HOST, data={"note": "n/a"})
        self.assertEqual(self.api("post", f"/manage/signup/{member.pk}/approve", token=token, host=CLUB_HOST).status_code, 200)

    def test_place_in_team(self):
        member, _membership = self.pending_member()
        team = Team.objects.create(club=self.club, name="U12")
        token = self.admin()
        response = self.api("post", f"/manage/signup/{member.pk}/place-in-team", token=token, host=CLUB_HOST, data={"team_id": str(team.pk)})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()["team"], response.json()["jersey_number"]), ("U12", None))
        self.assertTrue(TeamMembership.objects.filter(team=team, member=member, season=self.season).exists())
        self.assertError(self.api("post", f"/manage/signup/{member.pk}/place-in-team", token=token, host=CLUB_HOST, data={"team_id": str(team.pk)}), 409, "already_placed")
        row = next(r for r in self.api("get", "/manage/signup", token=token, host=CLUB_HOST).json()["items"] if r["member_id"] == str(member.pk))
        self.assertEqual([t["name"] for t in row["teams"]], ["U12"])

    def test_place_in_team_honours_requested_number(self):
        from registration.models import RegistrationBatch, RegistrationDetails

        member, membership = self.pending_member()
        pool = NumberPool.objects.create(club=self.club, name="Youth", min_number=1, max_number=30)
        team = Team.objects.create(club=self.club, name="U12", pool=pool)
        batch = RegistrationBatch.objects.create(club=self.club, season=self.season, contact_first_name="Pat", contact_last_name="Parent", contact_email="pat@example.com")
        details = RegistrationDetails.objects.create(membership=membership, batch=batch, entry_kind=RegistrationDetails.EntryKind.PLAYER, requested_team=team, requested_jersey_number=7, price=Decimal("10.00"))
        token = self.admin()
        response = self.api("post", f"/manage/signup/{member.pk}/place-in-team", token=token, host=CLUB_HOST, data={"team_id": str(team.pk)})
        self.assertEqual(response.json()["jersey_number"], 7)
        details.refresh_from_db()
        self.assertIsNotNone(details.resulting_team_membership_id)

    def test_place_in_other_clubs_team_is_422(self):
        other, _s, _m = self.other_club()
        foreign_team = Team.objects.create(club=other, name="Foreign")
        member, _membership = self.pending_member()
        token = self.admin()
        response = self.api("post", f"/manage/signup/{member.pk}/place-in-team", token=token, host=CLUB_HOST, data={"team_id": str(foreign_team.pk)})
        self.assertError(response, 422, "validation_error")
        self.assertIn("team_id", response.json()["error"]["fields"])

    def test_cancel(self):
        member, _membership = self.pending_member()
        token = self.admin()
        response = self.api("post", f"/manage/signup/{member.pk}/cancel", token=token, host=CLUB_HOST)
        self.assertEqual(response.json(), {"result": "cancelled"})
        self.assertFalse(ClubMembership.objects.filter(club=self.club, member=member, status="pending").exists())

    def test_link_to_existing_member(self):
        duplicate, duplicate_membership = self.pending_member("Kimmy")
        lapsed = Member.objects.create(first_name="Kim", last_name="Pending")
        old_season = Season.objects.create(club=self.club, start_date=datetime.date(2001, 1, 1), end_date=datetime.date(2001, 12, 1))
        ClubMembership.objects.create(club=self.club, member=lapsed, season=old_season, status="lapsed")
        token = self.admin()
        response = self.api("post", f"/manage/signup/{duplicate.pk}/link", token=token, host=CLUB_HOST, data={"target_member_id": str(lapsed.pk)})
        self.assertEqual(response.status_code, 200, response.content)
        duplicate_membership.refresh_from_db()
        self.assertEqual(duplicate_membership.member_id, lapsed.pk)

    def test_link_errors_are_422(self):
        member, _m = self.pending_member()
        token = self.admin()
        response = self.api("post", f"/manage/signup/{member.pk}/link", token=token, host=CLUB_HOST, data={"target_member_id": str(self.child.pk)})
        self.assertError(response, 422, "validation_error")
        self.assertIn("target_member_id", response.json()["error"]["fields"])
        stranger = self.other_club()[1]
        response = self.api("post", f"/manage/signup/{member.pk}/link", token=token, host=CLUB_HOST, data={"target_member_id": str(stranger.pk)})
        self.assertError(response, 422, "validation_error")


class RefereeEligibilityTests(OnboardingTestCase):
    def url(self, member=None):
        return f"/manage/members/{(member or self.child).pk}/referee-eligibility"

    def test_get_without_profile_is_empty(self):
        token = self.admin()
        self.assertEqual(self.api("get", self.url(), token=token, host=CLUB_HOST).json(), {"level_id": None, "valid_until": None, "excluded_team_ids": [], "eligible_team_ids": []})

    def test_put_creates_and_replaces(self):
        team_a, team_b = Team.objects.create(club=self.club, name="A"), Team.objects.create(club=self.club, name="B")
        level = RefereeLevel.objects.create(club=self.club, name="Regional")
        level.teams.set([team_a, team_b])
        token = self.admin()
        until = (datetime.date.today() + datetime.timedelta(days=100)).isoformat()
        response = self.api("put", self.url(), token=token, host=CLUB_HOST, data={"level_id": str(level.pk), "valid_until": until, "excluded_team_ids": [str(team_b.pk)]})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["level_id"], body["valid_until"], body["excluded_team_ids"], body["eligible_team_ids"]), (str(level.pk), until, [str(team_b.pk)], [str(team_a.pk)]))
        self.assertEqual(self.api("get", self.url(), token=token, host=CLUB_HOST).json(), body)
        cleared = self.api("put", self.url(), token=token, host=CLUB_HOST, data={})
        self.assertEqual((cleared.json()["level_id"], cleared.json()["excluded_team_ids"]), (None, []))
        self.assertEqual(RefereeProfile.objects.filter(club=self.club, member=self.child).count(), 1)

    def test_put_withdraws_open_invites_for_ineligible_games(self):
        from django.utils import timezone

        from events.models import RefereeSignup

        team = Team.objects.create(club=self.club, name="A")
        level = RefereeLevel.objects.create(club=self.club, name="Regional")
        level.teams.set([team])
        RefereeProfile.objects.create(club=self.club, member=self.child, level=level, valid_until=datetime.date.today() + datetime.timedelta(days=10))
        event = Event.objects.create(club=self.club, kind="game", title="Game", start=timezone.now() + datetime.timedelta(days=2))
        event.teams.add(team)
        invite = RefereeSignup.objects.create(event=event, member=self.child, status=RefereeSignup.Status.INVITED)
        token = self.admin()
        self.assertEqual(self.api("put", self.url(), token=token, host=CLUB_HOST, data={"level_id": None}).status_code, 200)
        self.assertFalse(RefereeSignup.objects.filter(pk=invite.pk).exists())

    def test_foreign_level_or_team_is_422(self):
        other, _s, _m = self.other_club()
        foreign_level = RefereeLevel.objects.create(club=other, name="Foreign")
        foreign_team = Team.objects.create(club=other, name="FT")
        token = self.admin()
        response = self.api("put", self.url(), token=token, host=CLUB_HOST, data={"level_id": str(foreign_level.pk), "excluded_team_ids": [str(foreign_team.pk)]})
        self.assertError(response, 422, "validation_error")
        self.assertEqual(set(response.json()["error"]["fields"]), {"level_id", "excluded_team_ids"})

    def test_invalid_date_is_422(self):
        token = self.admin()
        self.assertEqual(self.api("put", self.url(), token=token, host=CLUB_HOST, data={"valid_until": "soon"}).status_code, 422)


class OfficialEligibilityTests(OnboardingTestCase):
    def url(self):
        return f"/manage/members/{self.child.pk}/official-eligibility"

    def test_feature_off_is_404(self):
        token = self.admin()
        self.assertError(self.api("get", self.url(), token=token, host=CLUB_HOST), 404, "not_found")
        self.assertError(self.api("put", self.url(), token=token, host=CLUB_HOST, data={}), 404, "not_found")

    def test_get_and_put(self):
        get_waffle_flag_model().objects.get_or_create(name="officials")[0].clubs.add(self.club)
        level = OfficialLevel.objects.create(club=self.club, name="Table")
        token = self.admin()
        self.assertEqual(self.api("get", self.url(), token=token, host=CLUB_HOST).json(), {"level_id": None, "valid_until": None})
        until = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        response = self.api("put", self.url(), token=token, host=CLUB_HOST, data={"level_id": str(level.pk), "valid_until": until})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"level_id": str(level.pk), "valid_until": until})
        self.assertEqual(OfficialProfile.objects.get(club=self.club, member=self.child).level_id, level.pk)

    def test_foreign_level_is_422(self):
        get_waffle_flag_model().objects.get_or_create(name="officials")[0].clubs.add(self.club)
        other, _s, _m = self.other_club()
        foreign = OfficialLevel.objects.create(club=other, name="Foreign")
        token = self.admin()
        self.assertError(self.api("put", self.url(), token=token, host=CLUB_HOST, data={"level_id": str(foreign.pk)}), 422, "validation_error")


class PhotoTests(OnboardingTestCase):
    def url(self, member=None):
        return f"/manage/members/{(member or self.child).pk}/photo"

    def test_upload_and_delete(self):
        token = self.admin()
        response = self.send_multipart(self.url(), token, {"photo": png()})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNotNone(response.json()["player_photo_url"])
        self.child.refresh_from_db()
        self.assertTrue(self.child.player_photo)
        removed = self.api("delete", self.url(), token=token, host=CLUB_HOST)
        self.assertEqual(removed.status_code, 200)
        self.assertIsNone(removed.json()["player_photo_url"])
        self.child.refresh_from_db()
        self.assertFalse(self.child.player_photo)
        self.assertEqual(self.api("delete", self.url(), token=token, host=CLUB_HOST).status_code, 200)

    def test_replacing_keeps_one_photo(self):
        token = self.admin()
        self.send_multipart(self.url(), token, {"photo": png("one.png")})
        self.child.refresh_from_db()
        first = self.child.player_photo.name
        self.send_multipart(self.url(), token, {"photo": png("two.png")})
        self.child.refresh_from_db()
        self.assertNotEqual(self.child.player_photo.name, first)
        self.assertFalse(self.child.player_photo.storage.exists(first))

    def test_validation(self):
        token = self.admin()
        self.assertError(self.send_multipart(self.url(), token, {"photo": SimpleUploadedFile("a.txt", b"hi", content_type="text/plain")}), 415, "unsupported_media_type")
        self.assertError(self.send_multipart(self.url(), token, {"photo": SimpleUploadedFile("a.png", b"", content_type="image/png")}), 422, "validation_error")
        self.assertError(self.send_multipart(self.url(), token, {"photo": SimpleUploadedFile("a.png", b"x" * (10 * 1024 * 1024 + 1), content_type="image/png")}), 413, "file_too_large")
        fake = self.send_multipart(self.url(), token, {"photo": SimpleUploadedFile("a.png", b"not really a png", content_type="image/png")})
        self.assertError(fake, 422, "validation_error")
        self.assertIn("photo", fake.json()["error"]["fields"])
        self.assertEqual(self.send_multipart(self.url(), token).status_code, 422)
        self.child.refresh_from_db()
        self.assertFalse(self.child.player_photo)

    def test_shared_record_is_locked(self):
        token = self.admin()
        response = self.send_multipart(self.url(self.member), token, {"photo": png()})
        self.assertError(response, 409, "person_locked")
        self.assertError(self.api("delete", self.url(self.member), token=token, host=CLUB_HOST), 409, "person_locked")
