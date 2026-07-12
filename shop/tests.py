from django.test import TestCase

from club.models import Club

from .models import Product


class ProductSlugTests(TestCase):
    def setUp(self):
        self.club = Club.objects.create(name="Ajax United", slug="ajax-united")

    def test_slug_auto_populated_from_name(self):
        product = Product.objects.create(club=self.club, name="Home Jersey")

        self.assertEqual(product.slug, "home-jersey")

    def test_explicit_slug_is_preserved(self):
        product = Product.objects.create(club=self.club, name="Home Jersey", slug="custom")

        self.assertEqual(product.slug, "custom")

    def test_slug_is_unique_per_club_with_suffix(self):
        first = Product.objects.create(club=self.club, name="Home Jersey")
        second = Product.objects.create(club=self.club, name="Home Jersey")

        self.assertEqual(first.slug, "home-jersey")
        self.assertEqual(second.slug, "home-jersey-2")

    def test_same_slug_allowed_in_a_different_club(self):
        other = Club.objects.create(name="Rival FC", slug="rival-fc")
        here = Product.objects.create(club=self.club, name="Home Jersey")
        there = Product.objects.create(club=other, name="Home Jersey")

        self.assertEqual(here.slug, there.slug)

    def test_unsluggable_name_falls_back(self):
        product = Product.objects.create(club=self.club, name="###")

        self.assertEqual(product.slug, "item")
