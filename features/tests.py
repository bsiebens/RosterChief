from django.core.cache import cache
from django.test import RequestFactory, TestCase
from waffle import flag_is_active, get_waffle_flag_model

from club.models import Club

Flag = get_waffle_flag_model()


class ClubScopedFlagTests(TestCase):
    def setUp(self):
        # waffle caches flags by name, and its cache is NOT rolled back with the
        # test transaction -- a flag row recreated under the same name in the next
        # test would otherwise be shadowed by the previous test's cached object.
        cache.clear()
        self.addCleanup(cache.clear)
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")
        self.other = Club.objects.create(name="Rival FC", slug="rival-fc")
        self.flag = Flag.objects.create(name="shop")

    def request_for(self, club):
        request = RequestFactory().get("/")
        request.club = club
        request.user = None
        return request

    def active_for(self, club):
        return flag_is_active(self.request_for(club), "shop")

    def test_off_for_every_club_by_default(self):
        self.assertFalse(self.active_for(self.club))
        self.assertFalse(self.active_for(self.other))

    def test_on_only_for_the_targeted_club(self):
        self.flag.clubs.add(self.club)

        self.assertTrue(self.active_for(self.club))
        self.assertFalse(self.active_for(self.other))

    def test_removing_a_club_turns_it_off_again(self):
        self.flag.clubs.add(self.club)
        self.flag.clubs.remove(self.club)

        self.assertFalse(self.active_for(self.club))

    def test_everyone_true_overrides_club_targeting(self):
        self.flag.everyone = True
        self.flag.save()

        self.assertTrue(self.active_for(self.other))  # not targeted, still on

    def test_everyone_false_beats_club_targeting(self):
        # waffle's contract: `everyone` overrides ALL other settings, so a flag
        # switched off for everyone must stay off even for a targeted club.
        self.flag.clubs.add(self.club)
        self.flag.everyone = False
        self.flag.save()

        self.assertFalse(self.active_for(self.club))

    def test_no_club_on_the_request_is_not_active(self):
        # e.g. the base domain / control panel, where there is no tenant.
        self.flag.clubs.add(self.club)

        self.assertFalse(flag_is_active(self.request_for(None), "shop"))

    def test_is_active_for_club_without_a_request(self):
        self.flag.clubs.add(self.club)

        self.assertTrue(self.flag.is_active_for_club(self.club))
        self.assertFalse(self.flag.is_active_for_club(self.other))

    def test_is_active_for_club_respects_everyone(self):
        self.flag.everyone = False
        self.flag.save()
        self.assertFalse(self.flag.is_active_for_club(self.club))

        self.flag.everyone = True
        self.flag.save()
        self.assertTrue(self.flag.is_active_for_club(self.club))

    def test_cache_is_flushed_when_club_targeting_changes(self):
        # The M2M does not call save(), so without the flush signal waffle would
        # keep answering from a stale cached set.
        self.assertFalse(self.active_for(self.club))  # primes the cache

        self.flag.clubs.add(self.club)

        self.assertTrue(self.active_for(self.club))

    def test_cache_is_flushed_on_reverse_edit(self):
        self.assertFalse(self.active_for(self.club))

        self.club.flags.add(self.flag)

        self.assertTrue(self.active_for(self.club))
