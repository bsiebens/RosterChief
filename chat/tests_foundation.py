from django.test import SimpleTestCase

from authorization.catalog import CATALOG as PERMS
from features.catalog import AUTO_FEATURE_PLANS, CATALOG, DEFAULT_PLANS
from notifications.models import Category
from notifications.policy import POLICIES


class FoundationTests(SimpleTestCase):
    def test_chat_feature_is_known_and_in_all_access(self):
        self.assertIn("chat", CATALOG)
        self.assertIn("all_access", AUTO_FEATURE_PLANS)
        self.assertIn("chat", DEFAULT_PLANS["all_access"][1])

    def test_chat_permissions_are_feature_gated_and_not_admin(self):
        for code in ("chat.moderate", "chat.announce"):
            self.assertEqual(PERMS[code].feature, "chat")
            self.assertFalse(PERMS[code].admin)

    def test_every_permission_feature_exists(self):
        for perm in PERMS.values():
            if perm.feature:
                self.assertIn(perm.feature, CATALOG)

    def test_chat_notification_category_collapses(self):
        self.assertTrue(POLICIES[Category.CHAT].collapse)
