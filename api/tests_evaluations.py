"""Evaluations API (``/evaluations``): checklists, giving and reading evaluations, the player review and the meeting."""

import json
from unittest import mock

from django.test import Client, TestCase
from django_tenants.utils import schema_context

from authorization.services import assign_role, create_role
from controlpanel.tests import login_session
from evaluations.models import PlayerEvaluation
from features.entitlements import Entitlement, Entitlements
from formbuilder.models import Form, FormSend
from news.tests import NewsClubMixin

BASE = "/api/v1"

CRITERIA = [
    {"label": "Skating", "field_type": "number", "required": True, "section": "Skills"},
    {"label": "Shooting", "field_type": "number", "required": False, "section": "Skills"},
    {"label": "Attitude", "field_type": "choice", "required": False, "options": ["Poor", "Good", "Great"], "section": "Mindset"},
    {"label": "Remarks", "field_type": "textarea", "required": False},
]


class EvaluationsApiTests(NewsClubMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with schema_context(cls.club.schema_name):
            assign_role(cls.ann_user, create_role("Evaluation managers", ["evaluations.manage_evaluations"], slug="eval-managers"))
            assign_role(cls.bob_user, create_role("Evaluators", ["evaluations.execute_evaluations"], slug="evaluators"))

    def setUp(self):
        super().setUp()
        self.features = Entitlements({"evaluations": Entitlement(True)})
        for target in ("features.middleware.entitlements_for_request", "features.context.get_current_entitlements", "features.backends.get_current_entitlements"):
            patcher = mock.patch(target, side_effect=lambda *args: self.features)
            patcher.start()
            self.addCleanup(patcher.stop)
        door = mock.patch("members.services.access.can_access_management", return_value=True)
        door.start()
        self.addCleanup(door.stop)

    def call(self, method, path, user=None, data=None):
        headers = {"HTTP_HOST": self.host, "HTTP_X_SESSION_TOKEN": login_session(user or self.owner)}
        body = {"data": json.dumps(data), "content_type": "application/json"} if data is not None else {}
        return getattr(Client(), method)(BASE + path, **body, **headers)

    def checklist(self, name="U12", criteria=CRITERIA, **extra):
        r = self.call("post", "/evaluations/checklists", self.ann_user, {"name": name, **extra})
        self.assertEqual(r.status_code, 201, r.content)
        cid = r.json()["id"]
        if criteria is not None:
            r = self.call("put", f"/evaluations/checklists/{cid}/criteria", self.ann_user, {"fields": criteria})
            self.assertEqual(r.status_code, 200, r.content)
        return self.call("get", f"/evaluations/checklists/{cid}", self.ann_user).json()

    def evaluate(self, checklist, player, answers, user=None, **extra):
        return self.call("post", f"/evaluations/checklists/{checklist['id']}/evaluations", user or self.bob_user, {"player_id": str(player.pk), "answers": answers, **extra})

    # --- permissions --------------------------------------------------------------------------------------------

    def test_me_flags(self):
        me = lambda user: self.call("get", "/me", user).json()  # noqa: E731
        self.assertTrue(me(self.ann_user)["can_manage_evaluations"])
        self.assertFalse(me(self.ann_user)["can_execute_evaluations"])  # neither implies the other
        self.assertTrue(me(self.bob_user)["can_execute_evaluations"])
        self.assertFalse(me(self.bob_user)["can_manage_evaluations"])
        owner = me(self.owner)
        self.assertTrue(owner["can_manage_evaluations"] and owner["can_execute_evaluations"])  # the Owner role holds the whole catalog

    def test_feature_gate(self):
        self.features = Entitlements({})  # the club's plan has no evaluations
        me = self.call("get", "/me", self.owner).json()
        self.assertFalse(me["can_manage_evaluations"] or me["can_execute_evaluations"])
        self.assertEqual(self.call("get", "/evaluations/checklists", self.owner).status_code, 404)
        self.assertEqual(self.call("get", "/evaluations/checklists", self.cara_user).status_code, 403)  # permission first
        self.assertEqual(self.call("get", "/evaluations/checklists", self.bob_user).status_code, 403)  # the permission itself is dropped without the feature

    def test_nothing_without_a_permission(self):
        checklist = self.checklist()
        pk = checklist["id"]
        for method, path, data in [
            ("get", "/evaluations/checklists", None),
            ("get", f"/evaluations/checklists/{pk}", None),
            ("post", "/evaluations/checklists", {"name": "X"}),
            ("get", f"/evaluations/players/{self.ann.pk}", None),
            ("get", f"/evaluations/checklists/{pk}/stats", None),
            ("get", f"/evaluations/checklists/{pk}/meeting", None),
        ]:
            self.assertEqual(self.call(method, path, self.cara_user, data).status_code, 403, path)

    def test_manage_and_execute_are_separate(self):
        checklist = self.checklist()
        pk = checklist["id"]
        # A manager builds the rubric but never reads evaluations back; an evaluator reads and gives but never edits the rubric.
        self.assertEqual(self.call("get", f"/evaluations/players/{self.ann.pk}", self.ann_user).status_code, 403)
        self.assertEqual(self.call("get", f"/evaluations/checklists/{pk}/stats", self.ann_user).status_code, 403)
        self.assertEqual(self.evaluate(checklist, self.cara, {"skating": 3}, self.ann_user).status_code, 403)
        self.assertEqual(self.call("patch", f"/evaluations/checklists/{pk}", self.bob_user, {"name": "Hacked"}).status_code, 403)
        self.assertEqual(self.call("put", f"/evaluations/checklists/{pk}/criteria", self.bob_user, {"fields": []}).status_code, 403)
        self.assertEqual(self.call("delete", f"/evaluations/checklists/{pk}", self.bob_user).status_code, 403)
        # Both may read the definition (the evaluator needs it to fill it in).
        self.assertEqual(self.call("get", f"/evaluations/checklists/{pk}", self.bob_user).status_code, 200)
        self.assertEqual(self.call("get", "/evaluations/checklists", self.ann_user).status_code, 200)

    # --- checklists ---------------------------------------------------------------------------------------------

    def test_checklist_crud_and_hidden_from_forms(self):
        checklist = self.checklist("Goalkeepers", description="Netminders")
        self.assertEqual([f["key"] for f in checklist["fields"]], ["skating", "shooting", "attitude", "remarks"])
        self.assertEqual(checklist["criteria_count"], 4)
        listing = self.call("get", "/evaluations/checklists", self.ann_user).json()
        self.assertEqual(listing["count"], 1)
        # Its form is plumbing: it never shows up under Forms, and cannot be sent or opened there.
        self.assertEqual(self.call("get", "/forms").json()["count"], 0)
        form_id = Form.objects.get(evaluation_checklist__pk=checklist["id"]).pk
        self.assertEqual(self.call("get", f"/forms/{form_id}").status_code, 404)
        r = self.call("patch", f"/evaluations/checklists/{checklist['id']}", self.ann_user, {"name": "Keepers", "is_active": False})
        self.assertEqual((r.json()["name"], r.json()["is_active"]), ("Keepers", False))
        self.assertEqual(self.call("delete", f"/evaluations/checklists/{checklist['id']}", self.ann_user).status_code, 204)
        self.assertFalse(Form.objects.filter(pk=form_id).exists())

    def test_checklist_validation(self):
        self.assertEqual(self.call("post", "/evaluations/checklists", self.ann_user, {"name": "  "}).status_code, 422)
        c = self.checklist(criteria=None)
        r = self.call("put", f"/evaluations/checklists/{c['id']}/criteria", self.ann_user, {"fields": [{"label": "Photo", "field_type": "file"}]})
        self.assertEqual(r.status_code, 422)
        self.assertIn("fields.0.field_type", r.json()["errors"])

    def test_removed_criterion_with_answers_is_kept_deactivated(self):
        c = self.checklist()
        self.assertEqual(self.evaluate(c, self.cara, {"skating": 4}).status_code, 201)
        keep = [f for f in c["fields"] if f["key"] != "skating"]
        r = self.call("put", f"/evaluations/checklists/{c['id']}/criteria", self.ann_user, {"fields": keep})
        self.assertEqual(r.status_code, 200)
        skating = next(f for f in r.json()["fields"] if f["key"] == "skating")
        self.assertFalse(skating["is_active"])
        self.assertEqual(r.json()["criteria_count"], 3)

    # --- evaluating ---------------------------------------------------------------------------------------------

    def test_evaluate_and_read_back(self):
        c = self.checklist()
        r = self.evaluate(c, self.cara, {"skating": "4.5", "attitude": "Great", "remarks": "Fast"})
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body["season"]["id"], str(self.season.pk))  # the current season by default
        self.assertEqual(body["player"]["name"], "Cara Test")
        self.assertEqual(body["evaluator"]["name"], "Bob Test")
        self.assertEqual({a["key"]: a["value"] for a in body["answers"]}, {"skating": "4.5", "attitude": "Great", "remarks": "Fast"})
        listing = self.call("get", f"/evaluations/players/{self.cara.pk}", self.bob_user).json()
        self.assertEqual(listing["count"], 1)
        self.assertEqual(self.call("get", f"/evaluations/players/{self.ann.pk}", self.bob_user).json()["count"], 0)
        # No leftovers where forms are managed.
        self.assertEqual(self.call("get", "/form-sends").json()["count"], 0)
        self.assertFalse(FormSend.objects.filter(is_active=True).exists())

    def test_release_controls_what_the_player_sees(self):
        c = self.checklist()
        body = self.evaluate(c, self.cara, {"skating": "4.5", "remarks": "Fast"}, public_note="Great season").json()
        self.assertEqual((body["release"], body["public_note"]), ("", "Great season"))
        shared = lambda: self.call("get", "/portal/evaluations", self.cara_user).json()  # noqa: E731
        self.assertEqual(shared(), [])  # private until someone releases it
        # Only the evaluator or a checklist manager may release; Cara cannot, another evaluator-less user cannot.
        self.assertEqual(self.call("put", f"/evaluations/{body['id']}/release", self.cara_user, {"release": "full"}).status_code, 403)
        self.assertEqual(self.call("put", f"/evaluations/{body['id']}/release", self.bob_user, {"release": "bogus"}).status_code, 422)
        r = self.call("put", f"/evaluations/{body['id']}/release", self.bob_user, {"release": "notes", "public_note": "Great season, keep going"})
        self.assertEqual((r.status_code, r.json()["release"]), (200, "notes"), r.content)
        row = shared()[0]
        self.assertEqual((row["release"], row["public_note"], row["answers"]), ("notes", "Great season, keep going", []))
        self.call("put", f"/evaluations/{body['id']}/release", self.bob_user, {"release": "full"})
        self.assertEqual({a["label"] for a in shared()[0]["answers"]}, {"Skating"})  # free-text remarks stay with the staff unless marked visible
        self.call("put", f"/evaluations/{body['id']}/release", self.bob_user, {"release": ""})
        self.assertEqual(shared(), [])
        self.assertEqual(self.call("get", "/portal/evaluations", self.ann_user).json(), [])  # never to someone else

    def test_release_preview_and_apply(self):
        from notifications.models import InboxNotification

        c = self.checklist()
        e1 = self.evaluate(c, self.cara, {"skating": "4", "remarks": "private"}, public_note="Well done").json()
        e2 = self.evaluate(c, self.ann, {"skating": "3"}).json()
        self.assertEqual(self.call("get", f"/evaluations/checklists/{c['id']}/release", self.bob_user).json(), {"count": 2})
        preview = self.call("get", f"/evaluations/checklists/{c['id']}/release/preview", self.owner).json()  # the owner did not write them
        self.assertEqual(len(preview["results"]), 2)
        shown = {a["label"]: a["visible"] for a in preview["results"][0]["answers"] + preview["results"][1]["answers"]}
        self.assertTrue(shown["Skating"])
        self.assertFalse(shown["Remarks"])
        bad = self.call("put", f"/evaluations/checklists/{c['id']}/release", self.owner, {"items": [{"evaluation_id": e1["id"], "release": "bogus"}]})
        self.assertEqual(bad.status_code, 422)
        items = [{"evaluation_id": e1["id"], "release": "full", "public_note": "Great season"}, {"evaluation_id": e2["id"], "release": ""}]
        r = self.call("put", f"/evaluations/checklists/{c['id']}/release", self.owner, {"items": items})  # did not write either
        self.assertEqual((r.status_code, r.json()), (200, {"count": 1}), r.content)
        row = self.call("get", "/portal/evaluations", self.cara_user).json()
        self.assertEqual((len(row), row[0]["public_note"], [a["label"] for a in row[0]["answers"]]), (1, "Great season", ["Skating"]))
        self.assertEqual(InboxNotification.objects.filter(user=self.cara_user, title="A new evaluation is available").count(), 1)
        self.call("put", f"/evaluations/checklists/{c['id']}/release", self.owner, {"items": [{"evaluation_id": e1["id"], "release": "notes"}]})
        self.assertEqual(InboxNotification.objects.filter(user=self.cara_user, title="A new evaluation is available").count(), 1)  # no second ping
        self.assertEqual(self.call("get", f"/evaluations/checklists/{c['id']}/release", self.bob_user).json(), {"count": 1})

    def test_evaluate_validation(self):
        c = self.checklist()
        r = self.evaluate(c, self.cara, {"shooting": 3})  # the required "skating" is missing
        self.assertEqual(r.status_code, 422)
        self.assertIn("skating", r.json()["errors"])
        r = self.evaluate(c, self.cara, {"skating": "fast"})
        self.assertEqual(r.status_code, 422)
        r = self.evaluate(c, self.cara, {"skating": 3, "attitude": "Meh"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("attitude", r.json()["errors"])
        self.assertEqual(PlayerEvaluation.objects.count(), 0)
        empty = self.checklist("Empty", criteria=[{"label": "Optional", "field_type": "number", "required": False}])
        self.assertEqual(self.evaluate(empty, self.cara, {}).status_code, 422)  # nothing scored

    def test_number_range(self):
        c = self.checklist(criteria=[{"label": "Skating", "field_type": "number", "min_value": "1", "max_value": "5"}, {"label": "Open", "field_type": "number", "required": False, "min_value": "0"}])
        self.assertEqual([(f["min_value"], f["max_value"]) for f in c["fields"]], [("1.0000", "5.0000"), ("0.0000", None)])
        for bad in ("0", "5.5", "-1"):
            r = self.evaluate(c, self.cara, {"skating": bad})
            self.assertEqual(r.status_code, 422, bad)
            self.assertIn("skating", r.json()["errors"])
        self.assertEqual(self.evaluate(c, self.cara, {"skating": "1"}).status_code, 201)
        self.assertEqual(self.evaluate(c, self.cara, {"skating": "5", "open": 99}).status_code, 201)  # no upper limit on the second
        self.assertEqual(self.evaluate(c, self.cara, {"skating": 3, "open": -1}).status_code, 422)

    def test_number_range_rules(self):
        c = self.checklist(criteria=None)
        put = lambda fields: self.call("put", f"/evaluations/checklists/{c['id']}/criteria", self.ann_user, {"fields": fields})  # noqa: E731
        r = put([{"label": "A", "field_type": "number", "min_value": "5", "max_value": "1"}])
        self.assertEqual(r.status_code, 422)
        self.assertIn("fields.0.max_value", r.json()["errors"])
        r = put([{"label": "A", "field_type": "text", "min_value": "1"}])
        self.assertIn("fields.0.min_value", r.json()["errors"])

    def test_evaluate_rules(self):
        c = self.checklist()
        self.call("patch", f"/evaluations/checklists/{c['id']}", self.ann_user, {"is_active": False})
        self.assertEqual(self.evaluate(c, self.cara, {"skating": 3}).status_code, 422)  # retired checklists take no new evaluations
        blank = self.checklist("Blank", criteria=None)
        self.assertEqual(self.evaluate(blank, self.cara, {"skating": 3}).status_code, 422)
        c2 = self.checklist("Other")
        self.assertEqual(self.call("post", f"/evaluations/checklists/{c2['id']}/evaluations", self.bob_user, {"player_id": str(self.cara.pk), "season_id": "00000000-0000-0000-0000-000000000000", "answers": {"skating": 3}}).status_code, 422)
        self.assertEqual(self.call("post", f"/evaluations/checklists/{c2['id']}/evaluations", self.bob_user, {"player_id": "00000000-0000-0000-0000-000000000000", "answers": {"skating": 3}}).status_code, 404)

    def test_delete_evaluation_and_checklist_conflict(self):
        c = self.checklist()
        evaluation = self.evaluate(c, self.cara, {"skating": 3}).json()
        self.assertEqual(self.call("delete", f"/evaluations/checklists/{c['id']}", self.ann_user).status_code, 409)
        # Another evaluator may not remove it; its evaluator may, and so may a checklist manager.
        with schema_context(self.club.schema_name):
            assign_role(self.dan_user, create_role("More evaluators", ["evaluations.execute_evaluations"], slug="more"))
        self.assertEqual(self.call("delete", f"/evaluations/{evaluation['id']}", self.dan_user).status_code, 403)
        self.assertEqual(self.call("delete", f"/evaluations/{evaluation['id']}", self.bob_user).status_code, 204)
        again = self.evaluate(c, self.cara, {"skating": 3}).json()
        self.assertEqual(self.call("delete", f"/evaluations/{again['id']}", self.owner).status_code, 204)
        self.assertEqual(self.call("delete", f"/evaluations/checklists/{c['id']}", self.ann_user).status_code, 204)

    def test_seasons(self):
        rows = self.call("get", "/evaluations/seasons", self.bob_user).json()
        self.assertEqual([(r["id"], r["is_current"]) for r in rows], [(str(self.season.pk), True)])
        self.assertEqual(self.call("get", "/evaluations/seasons", self.ann_user).status_code, 403)

    # --- results ------------------------------------------------------------------------------------------------

    def test_stats_and_matrix(self):
        c = self.checklist()
        self.evaluate(c, self.cara, {"skating": 4, "attitude": "Good", "remarks": "ok"})
        self.evaluate(c, self.cara, {"skating": 2, "attitude": "Good"})
        self.evaluate(c, self.ann, {"skating": 3, "attitude": "Great"})
        stats = self.call("get", f"/evaluations/checklists/{c['id']}/stats", self.bob_user).json()
        self.assertEqual(stats["evaluation_count"], 3)
        rows = {r["field"]["key"]: r for r in stats["rows"]}
        self.assertEqual((rows["skating"]["average"], rows["skating"]["minimum"], rows["skating"]["maximum"]), (3.0, 2.0, 4.0))
        self.assertEqual(rows["attitude"]["distribution"], [["Good", 2], ["Great", 1]])
        self.assertEqual(rows["remarks"]["recent_answers"], ["ok"])
        self.assertEqual(rows["shooting"]["response_count"], 0)
        other = self.call("get", f"/evaluations/checklists/{c['id']}/stats?season_id={self.season.pk}", self.bob_user).json()
        self.assertEqual(other["evaluation_count"], 3)
        matrix = self.call("get", f"/evaluations/checklists/{c['id']}/matrix", self.bob_user).json()
        self.assertEqual([r["player"]["name"] for r in matrix["rows"]], ["Ann Test", "Cara Test"])
        skating_id = next(f["id"] for f in matrix["fields"] if f["key"] == "skating")
        cara = next(r for r in matrix["rows"] if r["player"]["name"] == "Cara Test")
        self.assertEqual(cara["values"][skating_id], "2")  # the latest answer

    def test_review_card_and_notes(self):
        c = self.checklist()
        self.evaluate(c, self.cara, {"skating": 3})
        self.evaluate(c, self.cara, {"skating": 5})
        review = self.call("get", f"/evaluations/checklists/{c['id']}/players/{self.cara.pk}", self.bob_user).json()
        self.assertEqual(len(review["evaluations"]), 2)
        skating = next(q for q in review["questions"] if q["field"]["key"] == "skating")
        self.assertEqual([p["value"] for p in skating["trend"]], [3.0, 5.0])  # oldest first
        self.assertEqual([e["value"] for e in skating["entries"]], ["5", "3"])  # newest first
        self.assertEqual([t["name"] for t in review["teams"]], ["Under 12"])
        r = self.call("post", f"/evaluations/checklists/{c['id']}/players/{self.cara.pk}/notes", self.bob_user, {"note": "Move up"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["author"]["name"], "Bob Test")
        self.assertEqual(self.call("post", f"/evaluations/checklists/{c['id']}/players/{self.cara.pk}/notes", self.bob_user, {"note": " "}).status_code, 422)
        review = self.call("get", f"/evaluations/checklists/{c['id']}/players/{self.cara.pk}", self.bob_user).json()
        self.assertEqual([n["note"] for n in review["notes"]], ["Move up"])
        self.assertEqual(self.call("delete", f"/evaluations/notes/{r.json()['id']}", self.bob_user).status_code, 204)

    def test_review_attendance_split_into_games_and_practices(self):
        import datetime

        from django.utils import timezone

        from events.models import Attendance, Event

        c = self.checklist()
        self.evaluate(c, self.cara, {"skating": 3})
        now = timezone.now()
        Status = Attendance.AttendanceStatus

        def event(kind, days, status):
            e = Event.objects.create(title=kind, kind=kind, start=now - datetime.timedelta(days=days), season=self.season)
            Attendance.objects.update_or_create(event=e, member=self.cara, defaults={"status": status})

        event("game", 5, Status.PRESENT)
        event("game", 4, Status.ABSENT)
        event("training", 3, Status.PRESENT)
        event("training", 2, Status.NO_RESPONSE)
        event("training", 1, Status.PRESENT)
        att = self.call("get", f"/evaluations/checklists/{c['id']}/players/{self.cara.pk}", self.bob_user).json()["attendance"]
        self.assertEqual((att["games"]["present"], att["games"]["absent"], att["games"]["rate"]), (1, 1, 50))
        self.assertEqual((att["practices"]["present"], att["practices"]["no_reply"], att["practices"]["rate"]), (2, 1, 100))
        self.assertEqual((att["present"], att["absent"], att["no_reply"]), (3, 1, 1))
        self.assertEqual([b["state"] for b in att["recent"]], ["present", "absent", "present", "no_reply", "present"])
        self.assertEqual(att["recent_limit"], 40)

    def test_players_list(self):
        c = self.checklist()
        self.evaluate(c, self.cara, {"skating": 3})
        self.evaluate(c, self.ann, {"skating": 3})
        players = self.call("get", f"/evaluations/checklists/{c['id']}/players", self.bob_user).json()
        self.assertEqual([p["name"] for p in players["results"]], ["Ann Test", "Cara Test"])
        future = self.call("get", f"/evaluations/checklists/{c['id']}/players?since=2999-01-01", self.bob_user).json()
        self.assertEqual(future["count"], 0)

    # --- the meeting --------------------------------------------------------------------------------------------

    def test_meeting_lifecycle(self):
        c = self.checklist()
        self.evaluate(c, self.cara, {"skating": 3})
        url = f"/evaluations/checklists/{c['id']}/meeting"
        self.assertEqual(self.call("get", url, self.bob_user).json(), {"active": None, "latest": None, "outcomes": []})
        started = self.call("post", url, self.bob_user).json()
        self.assertTrue(started["active"]["is_open"])
        self.assertEqual(self.call("post", url, self.bob_user).json()["active"]["id"], started["active"]["id"])  # starting twice reuses the open one
        sid = started["active"]["id"]
        r = self.call("put", f"/evaluations/sessions/{sid}/outcomes/{self.cara.pk}", self.bob_user, {"decision": "Move up to U14"})
        self.assertEqual(r.status_code, 200, r.content)
        self.call("put", f"/evaluations/sessions/{sid}/outcomes/{self.cara.pk}", self.bob_user, {"decision": "Stay in U12"})  # edited in place
        meeting = self.call("get", url, self.bob_user).json()
        self.assertEqual([(o["player"]["name"], o["decision"]) for o in meeting["outcomes"]], [("Cara Test", "Stay in U12")])
        pdf = self.call("get", f"/evaluations/sessions/{sid}/outcomes.pdf", self.bob_user)
        self.assertIn(pdf.status_code, (200, 503))
        ended = self.call("post", f"/evaluations/sessions/{sid}/end", self.bob_user).json()
        self.assertIsNone(ended["active"])
        self.assertFalse(ended["latest"]["is_open"])
        self.assertEqual(len(ended["outcomes"]), 1)  # the finished meeting's decisions stay downloadable
        self.assertEqual(self.call("put", f"/evaluations/sessions/{sid}/outcomes/{self.cara.pk}", self.bob_user, {"decision": "x"}).status_code, 422)
        again = self.call("post", url, self.bob_user).json()
        self.assertNotEqual(again["active"]["id"], sid)
        self.assertEqual(again["outcomes"], [])

    def test_blank_decision_removes_it(self):
        c = self.checklist()
        sid = self.call("post", f"/evaluations/checklists/{c['id']}/meeting", self.bob_user).json()["active"]["id"]
        self.call("put", f"/evaluations/sessions/{sid}/outcomes/{self.cara.pk}", self.bob_user, {"decision": "Release"})
        self.call("put", f"/evaluations/sessions/{sid}/outcomes/{self.cara.pk}", self.bob_user, {"decision": ""})
        self.assertEqual(self.call("get", f"/evaluations/checklists/{c['id']}/meeting", self.bob_user).json()["outcomes"], [])
