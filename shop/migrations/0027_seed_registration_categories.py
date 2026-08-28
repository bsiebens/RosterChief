from django.db import migrations

#: Mirrors shop.models.ProductCategory.RegistrationKind -- kept as plain
#: strings here since a data migration must use the historical model, not
#: the live enum (which could change shape after this migration ships).
_KINDS = (("player", "Player"), ("volunteer", "Volunteer"))


def seed_registration_categories(apps, schema_editor):
    Club = apps.get_model("club", "Club")
    ProductCategory = apps.get_model("shop", "ProductCategory")

    for club in Club.objects.all():
        for kind, name in _KINDS:
            category, created = ProductCategory.objects.get_or_create(club=club, name=name, defaults={"registration_kind": kind})
            if not created and not category.registration_kind:
                category.registration_kind = kind
                category.save(update_fields=["registration_kind"])


def unseed_registration_categories(apps, schema_editor):
    ProductCategory = apps.get_model("shop", "ProductCategory")
    ProductCategory.objects.filter(registration_kind__in=[kind for kind, _name in _KINDS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("shop", "0026_product_category_registration_kind"),
        ("club", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_registration_categories, unseed_registration_categories),
    ]
