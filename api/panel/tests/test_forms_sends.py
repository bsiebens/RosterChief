import datetime
from unittest import mock

from django.utils import timezone
from waffle import get_waffle_flag_model

from api.panel.tests.base_slice import SliceTestCase
from club.models import ClubRole
from formbuilder.models import Form, FormSend, Submission
from members.models import Group, GroupMembership
from teams.models import Team, TeamMembership

DISPATCH = "formbuilder.services.sends.dispatch_notify_form_send"


class FormSendsTests(SliceTestCase):
    def setUp(self):
        super().setUp()
        get_waffle_flag_model().objects.get_or_create(name="formbuilder")[0].clubs.add(self.club)
        self.form = Form.objects.create(club=self.club, title="Camp")
        self.url = f"/forms/{self.form.pk}/sends"
        self._token = None

    def token(self, role=ClubRole.Roles.ADMIN):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        if self._token is None:
            self._token = self.login_with_totp()
        return self._token

    def make_send(self, **kwargs):
        return FormSend.objects.create(club=self.club, form=self.form, **kwargs)

    def test_no_token_is_401(self):
        send = self.make_send()
        for method, path in (("get", self.url), ("post", self.url), ("post", self.url + "/preview"), ("get", f"{self.url}/{send.pk}"), ("patch", f"{self.url}/{send.pk}"), ("get", f"{self.url}/{send.pk}/audience")):
            self.assertError(self.api(method, "/manage" + path, host="ajax-united.rosterchief.app"), 401, "unauthenticated")

    def test_non_managers_are_403(self):
        send = self.make_send()
        token = self.token(ClubRole.Roles.MEMBER_ADMIN)
        for method, path in (("get", self.url), ("post", self.url), ("post", self.url + "/preview"), ("get", f"{self.url}/{send.pk}"), ("patch", f"{self.url}/{send.pk}"), ("get", f"{self.url}/{send.pk}/audience")):
            self.assertError(self.call(method, path, token, data={} if method in ("post", "patch") else None), 403, "forbidden")

    def test_feature_off_is_404(self):
        token = self.token()
        get_waffle_flag_model().objects.get(name="formbuilder").clubs.clear()
        self.assertError(self.call("get", self.url, token), 404, "feature_disabled")

    def test_other_club_is_404(self):
        other, _season, _stranger = self.other_club()
        form = Form.objects.create(club=other, title="Theirs")
        send = FormSend.objects.create(club=other, form=form)
        token = self.token()
        mine = self.make_send()
        for method, path, data in (
            ("get", f"/forms/{form.pk}/sends", None),
            ("post", f"/forms/{form.pk}/sends", {"club_wide": True}),
            ("post", f"/forms/{form.pk}/sends/preview", {"club_wide": True}),
            ("get", f"/forms/{form.pk}/sends/{send.pk}", None),
            ("patch", f"/forms/{form.pk}/sends/{send.pk}", {"is_active": False}),
            ("get", f"/forms/{form.pk}/sends/{send.pk}/audience", None),
            ("get", f"{self.url}/{send.pk}", None),
        ):
            self.assertError(self.call(method, path, token, data=data), 404, "not_found")
        self.assertTrue(FormSend.objects.get(pk=send.pk).is_active)
        self.assertEqual(self.call("get", f"{self.url}/{mine.pk}", token).status_code, 200)

    def test_create_club_wide_notifies(self):
        token = self.token()
        closes = timezone.now() + datetime.timedelta(days=7)
        with mock.patch(DISPATCH) as dispatch:
            response = self.call("post", self.url, token, data={"club_wide": True, "closes_at": closes.isoformat()})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        send = FormSend.objects.get(pk=body["id"])
        self.assertTrue(send.club_wide)
        self.assertEqual(send.created_by, self.member)
        self.assertEqual(body["audience_count"], 2)
        self.assertEqual(body["not_yet_count"], 2)
        self.assertTrue(body["is_open"])
        self.assertIsNone(body["public_token"])
        dispatch.assert_called_once_with(str(send.pk))

    def test_create_with_team_group_invited_excluded(self):
        team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        TeamMembership.objects.create(team=team, member=self.child, season=self.season, jersey_number=4)
        group = Group.objects.create(club=self.club, name="Parents")
        GroupMembership.objects.create(group=group, member=self.member)
        token = self.token()
        with mock.patch(DISPATCH):
            response = self.call("post", self.url, token, data={"team_ids": [str(team.pk)], "group_ids": [str(group.pk)], "excluded_member_ids": [str(self.member.pk)], "is_public": True})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["team_ids"], [str(team.pk)])
        self.assertEqual(body["audience_count"], 1)
        self.assertTrue(body["public_token"])

    def test_validation(self):
        team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        token = self.token()
        response = self.call("post", self.url, token, data={"club_wide": True, "team_ids": [str(team.pk)]})
        self.assertEqual(response.status_code, 422)
        self.assertIn("club_wide", response.json()["error"]["fields"])
        response = self.call("post", self.url, token, data={"team_ids": ["00000000-0000-0000-0000-000000000000"]})
        self.assertEqual(response.status_code, 422)
        self.assertIn("team_ids", response.json()["error"]["fields"])
        other, _season, _stranger = self.other_club()
        theirs = Team.objects.create(club=other, name="Theirs", short_name="T")
        self.assertEqual(self.call("post", self.url, token, data={"team_ids": [str(theirs.pk)]}).status_code, 422)
        self.assertFalse(FormSend.objects.exists())

    def test_non_admin_manager_needs_a_claim_and_cannot_go_club_wide(self):
        token = self.token(ClubRole.Roles.EDITOR)
        response = self.call("post", self.url, token, data={"club_wide": True})
        self.assertEqual(response.status_code, 422)
        self.assertIn("club_wide", response.json()["error"]["fields"])
        response = self.call("post", self.url, token, data={})
        self.assertEqual(response.status_code, 422)
        self.assertIn("non_field_errors", response.json()["error"]["fields"])
        with mock.patch(DISPATCH):
            self.assertEqual(self.call("post", self.url, token, data={"is_public": True}).status_code, 201)

    def test_create_is_idempotent(self):
        token = self.token()
        with mock.patch(DISPATCH) as dispatch:
            first = self.call("post", self.url, token, data={"club_wide": True}, headers={"Idempotency-Key": "k1"})
            second = self.call("post", self.url, token, data={"club_wide": True}, headers={"Idempotency-Key": "k1"})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(FormSend.objects.count(), 1)
        self.assertEqual(dispatch.call_count, 1)

    def test_preview_saves_nothing(self):
        token = self.token()
        with mock.patch(DISPATCH) as dispatch:
            response = self.call("post", self.url + "/preview", token, data={"club_wide": True})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual({member["name"] for member in body["members"]}, {str(self.member), str(self.child)})
        self.assertFalse(FormSend.objects.exists())
        dispatch.assert_not_called()
        self.assertEqual(self.call("post", self.url + "/preview", token, data={"club_wide": True, "team_ids": [str(Team.objects.create(club=self.club, name="A", short_name="A").pk)]}).status_code, 422)

    def test_list_and_detail(self):
        token = self.token()
        send = self.make_send(club_wide=True)
        Submission.objects.create(send=send, member=self.member)
        listed = self.call("get", self.url, token).json()["items"]
        self.assertEqual([row["id"] for row in listed], [str(send.pk)])
        self.assertEqual(listed[0]["submission_count"], 1)
        detail = self.call("get", f"{self.url}/{send.pk}", token).json()
        self.assertEqual(detail["audience_count"], 2)
        self.assertEqual(detail["not_yet_count"], 1)
        audience = self.call("get", f"{self.url}/{send.pk}/audience", token).json()
        self.assertEqual(audience["count"], 2)

    def test_update_pause_and_audience(self):
        token = self.token()
        send = self.make_send(club_wide=True)
        response = self.call("patch", f"{self.url}/{send.pk}", token, data={"is_active": False})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()["is_open"])
        send.refresh_from_db()
        self.assertFalse(send.is_active)
        self.assertTrue(send.club_wide)
        response = self.call("patch", f"{self.url}/{send.pk}", token, data={"excluded_member_ids": [str(self.child.pk)]})
        self.assertEqual(response.json()["audience_count"], 1)
        response = self.call("patch", f"{self.url}/{send.pk}", token, data={"max_submissions_per_user": -1})
        self.assertEqual(response.status_code, 422)
