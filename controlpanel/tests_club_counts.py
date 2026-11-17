"""The counts shown on the club list's status filter (active, archived, all)."""

from django.utils import timezone

from club.models import Club
from controlpanel.tests import META, PUBLIC, UserApiBase


def light_club(owner, name, **extra):
    """A club row without building its tenant schema (the counts only read the public table)."""
    club = Club(owner=owner, name=name, schema_name=name.lower().replace(" ", "_"), **extra)
    club.auto_create_schema = False
    club.save()
    return club


class ClubCountsTests(UserApiBase):
    def setUp(self):
        super().setUp()
        light_club(self.admin, "Sharks Counted")
        light_club(self.admin, "Eagles Counted")
        light_club(self.admin, "Hawks Counted", archived_at=timezone.now())

    def counts(self, query=""):
        r = self.client.get(f"/clubs/counts{query}", tenant=PUBLIC, META=dict(META), headers={"X-Session-Token": "x"})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_active_archived_and_all(self):
        body = self.counts()
        self.assertEqual((body["archived"] >= 1, body["all"]), (True, body["active"] + body["archived"]))
        self.assertEqual(self.counts("?q=Counted"), {"active": 2, "archived": 1, "all": 3})

    def test_counts_follow_the_search(self):
        self.assertEqual(self.counts("?q=Hawks"), {"active": 0, "archived": 1, "all": 1})
        self.assertEqual(self.counts("?q=Sharks%20Counted"), {"active": 1, "archived": 0, "all": 1})
        self.assertEqual(self.counts("?q=nothing-like-this"), {"active": 0, "archived": 0, "all": 0})

    def test_the_status_parameter_does_not_change_the_counts(self):
        self.assertEqual(self.counts("?q=Counted&status=archived"), {"active": 2, "archived": 1, "all": 3})
