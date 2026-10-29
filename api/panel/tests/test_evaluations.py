import uuid
from unittest import mock

from waffle import get_waffle_flag_model

from api.panel.tests.base_slice import SliceTestCase
from club.models import ClubRole, EvaluationManager
from evaluations.models import EvaluationChecklist, EvaluationNote, EvaluationOutcome, EvaluationSession
from evaluations.services import current_rubric_form, save_rubric, start_session, submit_evaluation
from management.pdf import PDFExportError

URL = "/evaluations"
CRITERIA = [
    {"label": "Ball control", "field_type": "number", "section": "Technical"},
    {"label": "Attitude", "field_type": "choice", "options": ["Poor", "Good"], "required": False},
]


class EvaluationsApiTests(SliceTestCase):
    def setUp(self):
        super().setUp()
        get_waffle_flag_model().objects.get_or_create(name="evaluations")[0].clubs.add(self.club)
        self._token = None

    def token(self, role=ClubRole.Roles.ADMIN):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": role})
        if self._token is None:
            self._token = self.login_with_totp()
        return self._token

    def make_checklist(self, name="U10", club=None, rows=None):
        checklist = EvaluationChecklist.objects.create(club=club or self.club, name=name)
        if rows is not False:
            save_rubric(checklist, rows or [{"label": "Ball control", "field_type": "number"}, {"label": "Attitude", "field_type": "choice", "options": ["Poor", "Good"], "required": False}])
            checklist.refresh_from_db()
        return checklist

    def evaluate(self, checklist, score="4", player=None):
        return submit_evaluation(club=self.club, checklist=checklist, player=player or self.child, season=self.season, evaluator=self.member, data={"ball-control": score, "attitude": "Good"})

    # --- access ---

    def test_no_token_is_401(self):
        for method, path in (("get", "/manage/evaluations/checklists"), ("post", "/manage/evaluations/checklists")):
            self.assertError(self.api(method, path, host="ajax-united.rosterchief.app"), 401, "unauthenticated")

    def test_plain_member_and_other_staff_are_403(self):
        checklist = self.make_checklist()
        self.assertError(self.call("get", URL + "/checklists", self.login()), 403, "forbidden")
        token = self.token(ClubRole.Roles.EDITOR)
        for method, path, data in (
            ("get", f"{URL}/checklists", None),
            ("get", f"{URL}/checklists/{checklist.slug}/stats", None),
            ("post", f"{URL}/checklists", {"name": "X"}),
            ("put", f"{URL}/checklists/{checklist.slug}/rubric", {"criteria": CRITERIA}),
        ):
            self.assertError(self.call(method, path, token, data=data), 403, "forbidden")

    def test_manager_reads_but_cannot_change_checklists_or_rubric(self):
        checklist = self.make_checklist()
        token = self.token(ClubRole.Roles.EDITOR)
        EvaluationManager.objects.create(club=self.club, member=self.member)
        for path in ("checklists", f"checklists/{checklist.slug}", f"checklists/{checklist.slug}/rubric", f"checklists/{checklist.slug}/stats", f"checklists/{checklist.slug}/matrix", f"checklists/{checklist.slug}/walkthrough"):
            self.assertEqual(self.call("get", f"{URL}/{path}", token).status_code, 200, path)
        for method, path, data in (
            ("post", "checklists", {"name": "X"}),
            ("post", f"checklists/{checklist.slug}/archive", None),
            ("post", f"checklists/{checklist.slug}/reactivate", None),
            ("put", f"checklists/{checklist.slug}/rubric", {"criteria": CRITERIA}),
        ):
            self.assertError(self.call(method, f"{URL}/{path}", token, data=data), 403, "forbidden")
        self.assertEqual(self.call("post", f"{URL}/checklists/{checklist.slug}/sessions", token).status_code, 201)

    def test_feature_off_is_404(self):
        checklist = self.make_checklist()
        token = self.token()
        get_waffle_flag_model().objects.get(name="evaluations").clubs.clear()
        for method, path, data in (("get", "checklists", None), ("post", "checklists", {"name": "X"}), ("get", f"checklists/{checklist.slug}/rubric", None), ("get", f"entries/{uuid.uuid4()}", None)):
            self.assertError(self.call(method, f"{URL}/{path}", token, data=data), 404, "feature_disabled")

    def test_other_clubs_objects_are_404(self):
        other, _season, _stranger = self.other_club()
        theirs = EvaluationChecklist.objects.create(club=other, name="Theirs")
        session = EvaluationSession.objects.create(club=other, checklist=theirs)
        token = self.token()
        player = uuid.uuid4()
        for method, path, data in (
            ("get", f"checklists/{theirs.slug}", None),
            ("get", f"checklists/{theirs.pk}", None),
            ("post", f"checklists/{theirs.slug}/archive", None),
            ("get", f"checklists/{theirs.slug}/rubric", None),
            ("put", f"checklists/{theirs.slug}/rubric", {"criteria": CRITERIA}),
            ("get", f"checklists/{theirs.slug}/stats", None),
            ("get", f"checklists/{theirs.slug}/matrix", None),
            ("get", f"checklists/{theirs.slug}/walkthrough", None),
            ("post", f"checklists/{theirs.slug}/sessions", None),
            ("post", f"checklists/{theirs.slug}/sessions/end", None),
            ("put", f"checklists/{theirs.slug}/players/{player}/outcome", {"decision": "x"}),
            ("post", f"checklists/{theirs.slug}/players/{player}/notes", {"note": "x"}),
            ("get", f"sessions/{session.pk}/outcomes.pdf", None),
            ("get", f"entries/{uuid.uuid4()}", None),
        ):
            self.assertError(self.call(method, f"{URL}/{path}", token, data=data), 404, "not_found")
        self.assertFalse(EvaluationSession.objects.filter(club=other, ended_at__isnull=False).exists())

    # --- checklists ---

    def test_create_list_get_archive(self):
        token = self.token()
        created = self.call("post", f"{URL}/checklists", token, data={"name": "Goalkeepers", "description": "GK squad"})
        self.assertEqual(created.status_code, 201, created.content)
        body = created.json()
        self.assertEqual((body["slug"], body["is_active"], body["has_rubric"], body["evaluation_count"]), ("goalkeepers", True, False, 0))
        self.assertEqual(EvaluationChecklist.objects.get(pk=body["id"]).club, self.club)
        twin = self.call("post", f"{URL}/checklists", token, data={"name": "Goalkeepers"}).json()
        self.assertEqual(twin["slug"], "goalkeepers-2")
        full = self.make_checklist("U8")
        self.evaluate(full)
        other, _season, _stranger = self.other_club()
        EvaluationChecklist.objects.create(club=other, name="Rival")
        listed = self.call("get", f"{URL}/checklists", token).json()["items"]
        self.assertEqual([(c["name"], c["evaluation_count"], c["has_rubric"]) for c in listed], [("Goalkeepers", 0, False), ("Goalkeepers", 0, False), ("U8", 1, True)])
        self.assertEqual(self.call("get", f"{URL}/checklists/{full.pk}", token).json()["slug"], "u8")

        archived = self.call("post", f"{URL}/checklists/u8/archive", token)
        self.assertEqual((archived.status_code, archived.json()["is_active"], archived.json()["evaluation_count"]), (200, False, 1))
        self.assertFalse(self.call("post", f"{URL}/checklists/u8/archive", token).json()["is_active"])
        self.assertTrue(self.call("post", f"{URL}/checklists/u8/reactivate", token).json()["is_active"])
        full.refresh_from_db()
        self.assertTrue(full.is_active)

    def test_create_validation_and_idempotency(self):
        token = self.token()
        self.assertError(self.call("post", f"{URL}/checklists", token, data={"name": ""}), 422, "validation_error")
        self.assertEqual(self.call("post", f"{URL}/checklists", token, data={"description": "x"}).status_code, 422)
        first = self.call("post", f"{URL}/checklists", token, data={"name": "Once"}, HTTP_IDEMPOTENCY_KEY="k1")
        again = self.call("post", f"{URL}/checklists", token, data={"name": "Once"}, HTTP_IDEMPOTENCY_KEY="k1")
        self.assertEqual((first.status_code, again.status_code, first.json()["id"] == again.json()["id"]), (201, 201, True))
        self.assertEqual(EvaluationChecklist.objects.filter(club=self.club, name="Once").count(), 1)

    # --- rubric ---

    def test_rubric_replace_and_read(self):
        token = self.token()
        checklist = EvaluationChecklist.objects.create(club=self.club, name="U12")
        empty = self.call("get", f"{URL}/checklists/u12/rubric", token).json()
        self.assertEqual((empty["version_id"], empty["criteria"]), (None, []))
        saved = self.call("put", f"{URL}/checklists/u12/rubric", token, data={"criteria": CRITERIA})
        self.assertEqual(saved.status_code, 200, saved.content)
        body = saved.json()
        self.assertTrue(body["checklist"]["has_rubric"])
        self.assertEqual([(c["key"], c["order"], c["section"], c["options"], c["required"]) for c in body["criteria"]], [("ball-control", 1, "Technical", [], True), ("attitude", 2, "", ["Poor", "Good"], False)])
        first_version = body["version_id"]
        again = self.call("put", f"{URL}/checklists/u12/rubric", token, data={"criteria": list(reversed(CRITERIA))}).json()
        self.assertNotEqual(again["version_id"], first_version)
        self.assertEqual([c["key"] for c in again["criteria"]], ["attitude", "ball-control"])
        self.assertEqual(self.call("get", f"{URL}/checklists/u12/rubric", token).json()["version_id"], again["version_id"])
        checklist.refresh_from_db()
        self.assertEqual(current_rubric_form(checklist).fields.count(), 2)

    def test_rubric_keeps_old_evaluations_readable(self):
        token = self.token()
        checklist = self.make_checklist()
        entry = self.evaluate(checklist)
        self.call("put", f"{URL}/checklists/{checklist.slug}/rubric", token, data={"criteria": [{"label": "Ball control", "field_type": "number"}]})
        detail = self.call("get", f"{URL}/entries/{entry.pk}", token).json()
        self.assertEqual([a["key"] for a in detail["answers"]], ["ball-control", "attitude"])

    def test_rubric_validation_422(self):
        token = self.token()
        checklist = self.make_checklist()
        path = f"{URL}/checklists/{checklist.slug}/rubric"
        empty = self.call("put", path, token, data={"criteria": []})
        self.assertError(empty, 422, "validation_error")
        self.assertIn("criteria", empty.json()["error"]["fields"])
        twice = self.call("put", path, token, data={"criteria": [{"label": "A"}, {"label": " a "}]})
        self.assertIn("criteria.1.label", twice.json()["error"]["fields"])
        choice = self.call("put", path, token, data={"criteria": [{"label": "A", "field_type": "choice"}]})
        self.assertIn("criteria.0.options", choice.json()["error"]["fields"])
        self.assertError(self.call("put", path, token, data={"criteria": [{"label": "A", "field_type": "file"}]}), 422, "validation_error")
        self.assertEqual(current_rubric_form(checklist).fields.count(), 2)

    # --- stats, matrix ---

    def test_stats_and_matrix(self):
        token = self.token()
        checklist = self.make_checklist()
        self.evaluate(checklist, "4")
        self.evaluate(checklist, "2")
        self.evaluate(checklist, "5", player=self.member)
        stats = self.call("get", f"{URL}/checklists/{checklist.slug}/stats", token).json()
        self.assertEqual((stats["total_evaluations"], stats["unique_players"], stats["unique_evaluators"], stats["show_all_seasons"], stats["season_id"] is not None), (3, 2, 1, False, True))
        numeric, distribution = stats["rows"]
        self.assertEqual((numeric["kind"], numeric["response_count"], numeric["minimum"], numeric["maximum"]), ("numeric", 3, 2.0, 5.0))
        self.assertAlmostEqual(numeric["average"], 11 / 3, places=4)
        self.assertEqual((distribution["kind"], distribution["distribution"]), ("distribution", [{"value": "Good", "count": 3}]))
        wide = self.call("get", f"{URL}/checklists/{checklist.slug}/stats?all_seasons=true", token).json()
        self.assertEqual((wide["season_id"], wide["show_all_seasons"]), (None, True))

        matrix = self.call("get", f"{URL}/checklists/{checklist.slug}/matrix", token).json()
        self.assertEqual([f["key"] for f in matrix["fields"]], ["ball-control", "attitude"])
        self.assertEqual({row["player"]["first_name"]: row["values"] for row in matrix["rows"]}, {"Kim": ["2", "Good"], "Pat": ["5", "Good"]})

    def test_stats_without_rubric_is_empty(self):
        token = self.token()
        checklist = self.make_checklist("Bare", rows=False)
        self.assertEqual(self.call("get", f"{URL}/checklists/{checklist.slug}/stats", token).json()["rows"], [])
        self.assertEqual(self.call("get", f"{URL}/checklists/{checklist.slug}/matrix", token).json(), {"fields": [], "rows": []})

    # --- walkthrough, meetings ---

    def test_walkthrough_empty_and_populated(self):
        token = self.token()
        checklist = self.make_checklist()
        path = f"{URL}/checklists/{checklist.slug}/walkthrough"
        empty = self.call("get", path, token).json()
        self.assertEqual((empty["players"], empty["current"], empty["session"], empty["latest_session"]), ([], None, None, None))
        self.evaluate(checklist, "3")
        self.evaluate(checklist, "5")
        self.evaluate(checklist, "1", player=self.member)
        body = self.call("get", path, token).json()
        self.assertEqual([p["first_name"] for p in body["players"]], ["Kim", "Pat"])
        current = body["current"]
        self.assertEqual((current["player"]["first_name"], current["previous_player_id"], current["next_player_id"], len(current["evaluations"])), ("Kim", None, str(self.member.pk), 2))
        ball = current["questions"][0]
        self.assertEqual([e["value"] for e in ball["entries"]], ["5", "3"])
        self.assertEqual([p["value"] for p in ball["trend"]], [3.0, 5.0])
        picked = self.call("get", f"{path}?player_id={self.member.pk}", token).json()["current"]
        self.assertEqual((picked["player"]["first_name"], picked["previous_player_id"], picked["next_player_id"]), ("Pat", str(self.child.pk), None))
        fallback = self.call("get", f"{path}?player_id={uuid.uuid4()}", token).json()["current"]
        self.assertEqual(fallback["player"]["first_name"], "Kim")
        self.assertEqual(self.call("get", f"{path}?since=2999-01-01", token).json()["players"], [])
        self.assertEqual(len(self.call("get", f"{path}?since=garbage", token).json()["players"]), 2)

    def test_meeting_outcomes_and_notes(self):
        token = self.token()
        checklist = self.make_checklist()
        self.evaluate(checklist)
        base = f"{URL}/checklists/{checklist.slug}"
        outcome_path = f"{base}/players/{self.child.pk}/outcome"
        self.assertError(self.call("put", outcome_path, token, data={"decision": "Move up"}), 409, "no_open_session")
        self.assertError(self.call("post", f"{base}/sessions/end", token), 409, "no_open_session")

        with self.captureOnCommitCallbacks(execute=True):
            started = self.call("post", f"{base}/sessions", token)
        self.assertEqual(started.status_code, 201, started.content)
        session_id = started.json()["id"]
        self.assertTrue(started.json()["is_open"])
        self.assertEqual(self.call("post", f"{base}/sessions", token).json()["id"], session_id)
        self.assertEqual(EvaluationSession.objects.filter(checklist=checklist).count(), 1)

        self.assertError(self.call("put", outcome_path, token, data={"decision": "  "}), 422, "validation_error")
        saved = self.call("put", outcome_path, token, data={"decision": " Move up "})
        self.assertEqual((saved.status_code, saved.json()["decision"]), (200, "Move up"))
        self.call("put", outcome_path, token, data={"decision": "Stay"})
        self.assertEqual(list(EvaluationOutcome.objects.values_list("decision", flat=True)), ["Stay"])
        self.assertError(self.call("put", f"{base}/players/{self.member.pk}/outcome", token, data={"decision": "x"}), 404, "not_found")

        note_path = f"{base}/players/{self.child.pk}/notes"
        self.assertError(self.call("post", note_path, token, data={"note": " "}), 422, "validation_error")
        first = self.call("post", note_path, token, data={"note": "Work on positioning"}, HTTP_IDEMPOTENCY_KEY="n1")
        again = self.call("post", note_path, token, data={"note": "Work on positioning"}, HTTP_IDEMPOTENCY_KEY="n1")
        self.assertEqual((first.status_code, again.json()["id"] == first.json()["id"]), (201, True))
        self.assertEqual(EvaluationNote.objects.count(), 1)
        self.assertEqual(first.json()["author"], "Pat Parent")
        self.assertError(self.call("post", f"{base}/players/{self.member.pk}/notes", token, data={"note": "x"}), 404, "not_found")

        walk = self.call("get", f"{base}/walkthrough", token).json()
        self.assertEqual(walk["session"]["id"], session_id)
        self.assertEqual((walk["current"]["outcome"]["decision"], [n["note"] for n in walk["current"]["notes"]]), ("Stay", ["Work on positioning"]))

        ended = self.call("post", f"{base}/sessions/end", token)
        self.assertEqual((ended.status_code, ended.json()["is_open"]), (200, False))
        after = self.call("get", f"{base}/walkthrough", token).json()
        self.assertEqual((after["session"], after["latest_session"]["id"]), (None, session_id))
        self.assertError(self.call("put", outcome_path, token, data={"decision": "x"}), 409, "no_open_session")

    # --- entry, pdf ---

    def test_entry_detail(self):
        token = self.token()
        checklist = self.make_checklist()
        entry = self.evaluate(checklist, "4")
        body = self.call("get", f"{URL}/entries/{entry.pk}", token).json()
        self.assertEqual((body["player"]["first_name"], body["checklist"]["slug"], body["evaluator"]["first_name"]), ("Kim", checklist.slug, "Pat"))
        self.assertEqual([(a["key"], a["value"]) for a in body["answers"]], [("ball-control", "4"), ("attitude", "Good")])

    def test_outcomes_pdf(self):
        token = self.token()
        checklist = self.make_checklist()
        session = start_session(club=self.club, checklist=checklist, started_by=self.member)
        path = f"{URL}/sessions/{session.pk}/outcomes.pdf"
        with mock.patch("api.panel.routers.evaluations.evaluation_outcomes_pdf", return_value=b"%PDF-1.4 x") as render:
            response = self.call("get", path, token)
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "application/pdf"))
        self.assertIn(f"outcomes-{checklist.slug}-", response["Content-Disposition"])
        self.assertEqual(response.content, b"%PDF-1.4 x")
        self.assertEqual(render.call_args.args[0]["rows"], [])
        with mock.patch("api.panel.routers.evaluations.evaluation_outcomes_pdf", side_effect=PDFExportError("no pdf")):
            self.assertError(self.call("get", path, token), 503, "pdf_unavailable")
