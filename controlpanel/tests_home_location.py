"""A club's home location through the platform API: the events.Location flagged ``is_home``."""

import uuid

from django_tenants.utils import schema_context

from club.services import create_club
from controlpanel import stats
from controlpanel.models import PlatformAuditLog
from controlpanel.tests import META, PUBLIC, UserApiBase, make_user
from events.models import Location

ARENA = {"name": "Sharks Arena", "address": "Sportlaan 1", "zip_code": "2800", "city": "Mechelen", "country": "BE"}


class _Base(UserApiBase):
    def setUp(self):
        super().setUp()
        self.club = create_club("Home FC", self.admin)
        self.path = f"/clubs/{self.club.pk}/home-location"

    def put(self, body, path=None):
        return self.client.put(path or self.path, json=body, tenant=PUBLIC, META=dict(META), headers={"X-Session-Token": "x"})

    def delete(self, path=None):
        return self.client.delete(path or self.path, tenant=PUBLIC, META=dict(META), headers={"X-Session-Token": "x"})

    def homes(self):
        with schema_context(self.club.schema_name):
            return list(Location.objects.filter(is_home=True))


class HomeLocationTests(_Base):
    def test_no_home_location_is_null(self):
        r = self.get(self.path)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json())
        self.assertIsNone(stats.club_overview(self.club)["home_location"])

    def test_set_creates_the_location_and_reads_back(self):
        r = self.put(ARENA)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual({k: body[k] for k in ARENA}, ARENA)
        self.assertEqual(body["country_name"], "Belgium")
        self.assertEqual(self.get(self.path).json(), body)
        (home,) = self.homes()
        self.assertEqual((str(home.pk), home.name, home.is_home), (body["id"], "Sharks Arena", True))
        self.assertEqual(stats.club_overview(self.club)["home_location"]["name"], "Sharks Arena")
        self.assertEqual(stats.club_overview(self.club)["home_location"]["id"], body["id"])  # JSON-friendly

    def test_setting_again_edits_the_same_row(self):
        first = self.put(ARENA).json()
        second = self.put({**ARENA, "name": "New Arena", "city": "Leuven", "country": "nl"}).json()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual((second["name"], second["city"], second["country"], second["country_name"]), ("New Arena", "Leuven", "NL", "Netherlands"))
        with schema_context(self.club.schema_name):
            self.assertEqual(Location.objects.count(), 1)

    def test_values_are_trimmed(self):
        body = self.put({**ARENA, "name": "  Padded  ", "city": " Mechelen "}).json()
        self.assertEqual((body["name"], body["city"]), ("Padded", "Mechelen"))

    def test_other_locations_are_left_alone(self):
        with schema_context(self.club.schema_name):
            away = Location.objects.create(name="Away Hall", address="x", city="y", zip_code="z", country="NL")
        self.put(ARENA)
        with schema_context(self.club.schema_name):
            away.refresh_from_db()
            self.assertFalse(away.is_home)
            self.assertEqual(Location.objects.count(), 2)

    def test_never_two_homes(self):
        with schema_context(self.club.schema_name):
            Location.objects.create(name="One", address="a", city="b", zip_code="c", country="BE", is_home=True)
            Location.objects.create(name="Two", address="a", city="b", zip_code="c", country="BE", is_home=True)
        self.put(ARENA)
        self.assertEqual(len(self.homes()), 1)

    def test_validation(self):
        r = self.put({**ARENA, "country": "ZZ"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("country", r.json()["error"]["fields"])
        r = self.put({**ARENA, "name": "   ", "city": ""})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(set(r.json()["error"]["fields"]), {"name", "city"})
        r = self.put({"name": "x"})  # required fields missing: the schema rejects it
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.homes(), [])  # nothing was saved

    def test_clear_keeps_the_location_but_not_as_home(self):
        self.put(ARENA)
        self.assertEqual(self.delete().status_code, 204)
        self.assertIsNone(self.get(self.path).json())
        with schema_context(self.club.schema_name):
            self.assertEqual(Location.objects.filter(is_home=False, name="Sharks Arena").count(), 1)
        r = self.delete()
        self.assertEqual(r.status_code, 404)

    def test_unknown_club_is_404(self):
        bogus = f"/clubs/{uuid.uuid4()}/home-location"
        self.assertEqual(self.get(bogus).status_code, 404)
        self.assertEqual(self.put(ARENA, bogus).status_code, 404)
        self.assertEqual(self.delete(bogus).status_code, 404)

    def test_staff_may_manage_it(self):
        self.current = make_user("sam@example.com", level="staff")
        self.assertEqual(self.put(ARENA).status_code, 200)
        self.assertEqual(self.delete().status_code, 204)

    def test_changes_are_audited(self):
        self.put(ARENA)
        self.put({**ARENA, "city": "Leuven"})
        self.delete()
        actions = list(PlatformAuditLog.objects.filter(target_id=str(self.club.pk)).order_by("created").values_list("action", "data"))
        self.assertEqual([a for a, _ in actions], ["club.home_location_set", "club.home_location_set", "club.home_location_cleared"])
        self.assertTrue(actions[0][1]["created"])
        self.assertFalse(actions[1][1]["created"])


class HomeLocationCoordinateTests(_Base):
    """Optional exact position: for when the address lookup puts the pin in the wrong place."""

    def test_no_coordinates_by_default(self):
        body = self.put(ARENA).json()
        self.assertEqual((body["latitude"], body["longitude"]), (None, None))

    def test_coordinates_are_stored_and_read_back(self):
        body = self.put({**ARENA, "latitude": 51.02871234567, "longitude": 4.47731}).json()
        self.assertEqual((body["latitude"], body["longitude"]), (51.028712, 4.47731))
        self.assertEqual(self.get(self.path).json()["latitude"], 51.028712)
        self.assertEqual(stats.club_overview(self.club)["home_location"]["longitude"], 4.47731)

    def test_coordinates_can_be_removed_again(self):
        self.put({**ARENA, "latitude": 51.0, "longitude": 4.0})
        body = self.put(ARENA).json()
        self.assertEqual((body["latitude"], body["longitude"]), (None, None))

    def test_both_or_neither(self):
        r = self.put({**ARENA, "latitude": 51.0})
        self.assertEqual(r.status_code, 422)
        self.assertIn("longitude", r.json()["error"]["fields"])
        r = self.put({**ARENA, "longitude": 4.0})
        self.assertIn("latitude", r.json()["error"]["fields"])
        self.assertEqual(self.homes(), [])

    def test_ranges(self):
        r = self.put({**ARENA, "latitude": 91, "longitude": 181})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(set(r.json()["error"]["fields"]), {"latitude", "longitude"})
