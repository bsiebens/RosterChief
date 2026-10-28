"""One home game, three people in one family with three different roles: a child who plays, the
parent who can referee, a second child who can officiate. What the app must receive for it."""

import datetime

from django.core.cache import cache
from django.utils import timezone
from waffle import get_waffle_flag_model

from club.models import ClubMembership
from events.models import Event, Location
from members.models import Family, FamilyMembership, Member
from teams.models import OfficialLevel, OfficialProfile, Position, RefereeLevel, RefereeProfile, Team, TeamMembership

from .test_member import ClubApiTestCase


class FamilyWithThreeRolesTests(ClubApiTestCase):
    def setUp(self):
        super().setUp()
        flag, _created = get_waffle_flag_model().objects.get_or_create(name="officials")
        flag.clubs.add(self.club)
        cache.clear()

        team = Team.objects.create(club=self.club, name="U12", short_name="U12")
        TeamMembership.objects.create(team=team, member=self.child, season=self.season, position=Position.objects.create(club=self.club, name="Forward", short_name="F"))
        self.sibling = Member.objects.create(first_name="Sam", last_name="Parent", date_of_birth=datetime.date(2010, 1, 1))
        ClubMembership.objects.create(club=self.club, member=self.sibling, season=self.season, status=ClubMembership.StatusChoices.ACTIVE)
        FamilyMembership.objects.create(family=Family.objects.get(club=self.club), member=self.sibling, role=FamilyMembership.FamilyRole.CHILD)

        referee_level = RefereeLevel.objects.create(club=self.club, name="Regional")
        referee_level.teams.add(team)
        official_level = OfficialLevel.objects.create(club=self.club, name="Table")
        official_level.teams.add(team)
        valid = timezone.localdate() + datetime.timedelta(days=30)
        RefereeProfile.objects.create(club=self.club, member=self.member, level=referee_level, valid_until=valid)
        OfficialProfile.objects.create(club=self.club, member=self.sibling, level=official_level, valid_until=valid)

        ground = Location.objects.create(club=self.club, name="Home Ground", address="1 St", city="Town", zip_code="1000", country="BE", is_home=True)
        self.game = Event.objects.create(club=self.club, title="Big game", kind=Event.EventKind.GAME, start=self.soon, location=ground, season=self.season)
        self.game.teams.add(team)

    def test_the_calendar_returns_one_item_per_role_for_the_same_game(self):
        items = self.get("/calendar").json()["items"]

        self.assertEqual({item["event"]["id"] for item in items}, {str(self.game.pk)})
        self.assertEqual(sorted((item["type"], item["member_id"], item["status"]) for item in items), sorted([("rsvp", str(self.child.pk), "no_response"), ("referee", str(self.member.pk), "invited"), ("official", str(self.sibling.pk), "invited")]))

    def test_event_detail_lists_the_players_answer_and_both_signups(self):
        detail = self.get(f"/events/{self.game.pk}").json()

        self.assertEqual([answer["member_id"] for answer in detail["answers"]], [str(self.child.pk)])
        self.assertEqual([signup["member_id"] for signup in detail["referee_signups"]], [str(self.member.pk)])
        self.assertEqual([signup["member_id"] for signup in detail["official_signups"]], [str(self.sibling.pk)])

    def test_home_asks_for_all_three_answers(self):
        home = self.get("/home").json()

        self.assertEqual([(card["kind"], [row["member_id"] for row in card["members"]]) for card in home["member_cards"]], [("rsvp_needed", [str(self.child.pk)])])
        self.assertEqual(sorted((row["type"], row["member_id"]) for row in home["needs_answer"]), sorted([("referee", str(self.member.pk)), ("official", str(self.sibling.pk))]))

    def test_without_the_officials_feature_only_the_referee_and_player_remain(self):
        get_waffle_flag_model().objects.get(name="officials").clubs.remove(self.club)
        cache.clear()

        items = self.get("/calendar").json()["items"]

        self.assertEqual(sorted(item["type"] for item in items), ["referee", "rsvp"])

    def test_each_calendar_row_has_a_unique_stable_id(self):
        items = self.get("/calendar").json()["items"]

        ids = [item["id"] for item in items]
        self.assertEqual(len(set(ids)), 3)  # three rows, one game: the event id alone can't tell them apart
        self.assertEqual(sorted(ids), sorted([f"rsvp:{self.child.pk}:{self.game.pk}", f"referee:{self.member.pk}:{self.game.pk}", f"official:{self.sibling.pk}:{self.game.pk}"]))
        self.assertEqual(ids, [item["id"] for item in self.get("/calendar").json()["items"]])

    def test_event_detail_keeps_a_declined_duty_until_the_game_starts_like_the_calendar(self):
        from events.models import OfficialSignup, RefereeSignup

        RefereeSignup.objects.filter(event=self.game, member=self.member).update(status=RefereeSignup.Status.DECLINED)
        OfficialSignup.objects.filter(event=self.game, member=self.sibling).update(status=OfficialSignup.Status.DECLINED)

        detail = self.get(f"/events/{self.game.pk}").json()
        calendar = {item["type"]: item["status"] for item in self.get("/calendar").json()["items"]}

        self.assertEqual([signup["status"] for signup in detail["referee_signups"]], ["declined"])
        self.assertEqual([signup["status"] for signup in detail["official_signups"]], ["declined"])
        self.assertEqual((calendar["referee"], calendar["official"]), ("declined", "declined"))

    def test_a_declined_duty_leaves_event_detail_once_the_game_has_started(self):
        from events.models import RefereeSignup

        RefereeSignup.objects.filter(event=self.game, member=self.member).update(status=RefereeSignup.Status.DECLINED)
        Event.objects.filter(pk=self.game.pk).update(start=timezone.now() - datetime.timedelta(minutes=5))

        self.assertEqual(self.get(f"/events/{self.game.pk}").json()["referee_signups"], [])
