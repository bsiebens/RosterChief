"""A club's primary and secondary colour through the platform API."""

from club.models import Sport
from club.services import create_club
from controlpanel.tests import META, PUBLIC, UserApiBase


class ClubColorTests(UserApiBase):
    def setUp(self):
        super().setUp()
        self.club = create_club("Color FC", self.admin)
        self.club.sport = Sport.objects.create(name="Basketball")
        self.club.save()
        self.path = f"/clubs/{self.club.pk}"

    def patch(self, body):
        return self.client.patch(self.path, json=body, tenant=PUBLIC, META=dict(META), headers={"X-Session-Token": "x"})

    def test_set_read_and_reset(self):
        r = self.patch({"primary_color": "#0a2540", "secondary_color": "#e4002b"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["primary_color"], r.json()["secondary_color"]), ("#0a2540", "#e4002b"))
        self.assertEqual(self.get(self.path).json()["primary_color"], "#0a2540")
        r = self.patch({"secondary_color": ""})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["primary_color"], r.json()["secondary_color"]), ("#0a2540", ""))

    def test_rejects_bad_values(self):
        for bad in ("red", "#12345", "#ffffff"):
            r = self.patch({"primary_color": bad})
            self.assertEqual(r.status_code, 422, (bad, r.content))
