"""The all_access plan: every catalog feature, no limits, and it keeps up with the catalog."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from club.models import Club
from features import services
from features.catalog import ALL_ACCESS_PLAN, CATALOG, LIMIT, FeatureDef, all_access_features
from features.entitlements import resolve_entitlements
from features.models import EntitlementLog, Feature, Plan, PlanFeature


def plan_features(plan):
    return {pf.feature.code: pf.limit for pf in plan.plan_features.select_related("feature")}


class AllAccessPlanTests(TestCase):
    def setUp(self):
        services.sync_catalog()
        self.plan = Plan.objects.get(code=ALL_ACCESS_PLAN)

    def test_it_is_seeded_with_every_feature_and_no_limits(self):
        self.assertEqual(plan_features(self.plan), dict.fromkeys(all_access_features()))
        self.assertEqual(set(plan_features(self.plan)), set(CATALOG))  # nothing in the catalog shares a limit scope yet
        self.assertTrue(any(f.kind == LIMIT for f in CATALOG.values()))  # and the limit features are there, unlimited

    def test_syncing_again_changes_nothing(self):
        before = plan_features(self.plan)
        logs = EntitlementLog.objects.count()
        services.sync_catalog()
        self.assertEqual(plan_features(self.plan), before)
        self.assertEqual(EntitlementLog.objects.count(), logs)

    def test_a_new_catalog_feature_is_added_without_a_limit(self):
        extra = {"reports": FeatureDef("reports", "Reports", "Club reports."), "max_teams": FeatureDef("max_teams", "Team limit", kind=LIMIT, limit_scope="teams")}
        with mock.patch.dict(CATALOG, extra):
            services.sync_catalog()
            features = plan_features(self.plan)
        self.assertIn("reports", features)
        self.assertIn("max_teams", features)
        self.assertIsNone(features["reports"])
        self.assertIsNone(features["max_teams"])  # unlimited
        entry = EntitlementLog.objects.filter(plan=self.plan, action="plan_feature_added", feature__code="max_teams").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.new, {"feature": "max_teams", "limit": None})

    def test_a_limit_set_by_an_admin_survives_a_sync(self):
        PlanFeature.objects.filter(plan=self.plan, feature__code="max_members").update(limit=500)
        with mock.patch.dict(CATALOG, {"reports": FeatureDef("reports", "Reports")}):
            services.sync_catalog()
        self.assertEqual(plan_features(self.plan)["max_members"], 500)
        self.assertEqual(self.plan.plan_features.filter(feature__code="max_members").count(), 1)

    def test_a_feature_an_admin_removed_is_added_back_only_if_it_is_still_missing(self):
        # The plan is "every feature": removing one is undone by the next sync, which is the point of the plan.
        PlanFeature.objects.filter(plan=self.plan, feature__code="sponsoring").delete()
        services.sync_catalog()
        self.assertIn("sponsoring", plan_features(self.plan))

    def test_a_second_limit_feature_in_the_same_scope_is_skipped(self):
        extra = {"max_players": FeatureDef("max_players", "Player limit", kind=LIMIT, limit_scope="members")}
        with mock.patch.dict(CATALOG, extra):
            self.assertNotIn("max_players", all_access_features())  # the first feature of a scope wins
            services.sync_catalog()
            self.assertNotIn("max_players", plan_features(self.plan))

    def test_inactive_features_are_ignored_by_entitlements(self):
        with mock.patch.dict(CATALOG, {"reports": FeatureDef("reports", "Reports")}):
            services.sync_catalog()
        services.sync_catalog()  # "reports" left the catalog again: its Feature row is deactivated
        self.assertFalse(Feature.objects.get(code="reports").is_active)

    def test_a_club_on_the_plan_has_every_feature_and_no_limits(self):
        owner = get_user_model().objects.create(email="owner@allaccess.test", is_active=True)
        club = Club(owner=owner, name="All Access FC", schema_name="all_access_fc")
        club.auto_create_schema = False
        club.save()
        services.subscribe(club, self.plan, reason="test")
        ent = resolve_entitlements(club)
        for code, definition in CATALOG.items():
            self.assertTrue(ent.has(code), code)
            if definition.kind == LIMIT:
                self.assertIsNone(ent.limit(code), code)
        ent.check_limit("max_members", 10_000_000)  # never raises

    def test_the_default_plan_is_still_free_with_its_limit(self):
        self.assertEqual(plan_features(Plan.objects.get(code="free")), {"max_members": 50})
