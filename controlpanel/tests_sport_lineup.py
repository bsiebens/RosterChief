"""Platform admins switch line-up lines on for a sport and edit its template."""

from club.models import Sport
from controlpanel.models import PlatformAuditLog
from controlpanel.tests import PUBLIC, META, UserApiBase

TEMPLATE = [
    {"key": "goalies", "label": "Goalies", "slots": [{"key": "g1", "label": "G1"}, {"key": "g2", "label": "G2"}]},
    {"label": "Line 1", "slots": [{"label": "LW"}, {"label": "C"}, {"label": "RW"}]},
]


class SportLineupSlotsTests(UserApiBase):
    def put(self, sport, body):
        return self.client.put(f"/sports/{sport.pk}/lineup-slots", json=body, tenant=PUBLIC, META=dict(META), headers={"X-Session-Token": "x"})

    def test_enable_with_template_generates_keys_and_audits(self):
        sport = Sport.objects.create(name="Ice hockey")
        r = self.put(sport, {"enabled": True, "template": TEMPLATE})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body["lineup_slots_enabled"])
        self.assertEqual(body["lineup_template"][1]["key"], "line-1")
        self.assertEqual([s["key"] for s in body["lineup_template"][1]["slots"]], ["lw", "c", "rw"])
        sport.refresh_from_db()
        self.assertTrue(sport.lineup_slots_enabled)
        self.assertTrue(PlatformAuditLog.objects.filter(action="sport.lineup_slots_changed", target_id=str(sport.pk)).exists())

    def test_validation(self):
        sport = Sport.objects.create(name="Ice hockey")
        bad = [
            {"enabled": True, "template": []},
            {"enabled": False, "template": [{"key": "a", "label": "", "slots": [{"label": "x"}]}]},
            {"enabled": False, "template": [{"key": "a", "label": "A", "slots": [{"key": "x", "label": "X"}]}, {"key": "a", "label": "B", "slots": [{"label": "y"}]}]},
            {"enabled": False, "template": [{"key": "a", "label": "A", "slots": [{"key": "x", "label": "X"}, {"key": "x", "label": "Y"}]}]},
            {"enabled": False, "template": [{"key": "a.b", "label": "A", "slots": [{"label": "X"}]}]},
            {"enabled": False, "template": [{"key": "a", "label": "A", "slots": []}]},
            {"enabled": False, "template": [{"label": f"G{i}", "slots": [{"label": "x"}]} for i in range(13)]},
            {"enabled": False, "template": [{"label": "A", "slots": [{"label": "x" * 41}]}]},
        ]
        for body in bad:
            r = self.put(sport, body)
            self.assertEqual(r.status_code, 422, body)
            self.assertIn("template", r.json()["error"]["fields"], body)
        sport.refresh_from_db()
        self.assertFalse(sport.lineup_slots_enabled)

    def test_switch_off_keeps_template(self):
        sport = Sport.objects.create(name="Ice hockey")
        self.put(sport, {"enabled": True, "template": TEMPLATE})
        r = self.put(sport, {"enabled": False, "template": TEMPLATE})
        self.assertFalse(r.json()["lineup_slots_enabled"])
        self.assertEqual(len(r.json()["lineup_template"]), 2)
