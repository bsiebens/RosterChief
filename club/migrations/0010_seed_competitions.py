"""Seed the competitions clubs can import fixtures from (public schema).

One club.Competition per data source of events.competition.hockey (``module`` + class ``name``), each with its ice hockey
Sport and the catalog feature that unlocks it (features.catalog: competition_rbihf / competition_dfel /
competition_ijnl). Feature rows are normally mirrored from the catalog after migrate (features.signals.sync_features);
they are get_or_create'd here with the catalog's own code/name/description so the competitions can point at them on a
fresh database. CEHL has its own feature too (competition_cehl): no club is offered a competition until the platform
links a feature (as the old flagless competitions).

Idempotent (get_or_create; an existing row with the same name is only completed, never overwritten). Reverse removes the
seeded competitions, and the features/sport only when nothing else uses them.
"""

from django.db import migrations
from django_tenants.utils import get_public_schema_name

MODULE = "events.competition.hockey"
SPORT_NAME = "Ice hockey"

# Identical to features/catalog.py.
FEATURES = {
    "competition_rbihf": ("RBIHF fixtures", "Import fixtures from the RBIHF competition."),
    "competition_dfel": ("DFEL fixtures", "Import fixtures from the DFEL competition."),
    "competition_ijnl": ("IJNL fixtures", "Import fixtures from the IJNL competition."),
    "competition_cehl": ("CEHL fixtures", "Import fixtures from the CEHL competition."),
}

COMPETITIONS = {"RBIHF": "competition_rbihf", "CEHL": "competition_cehl", "DFEL": "competition_dfel", "IJNL": "competition_ijnl"}


def seed(apps, schema_editor):
    if getattr(schema_editor.connection, "schema_name", get_public_schema_name()) != get_public_schema_name():
        return  # shared tables: only the public schema has them
    Sport = apps.get_model("club", "Sport")
    Competition = apps.get_model("club", "Competition")
    Feature = apps.get_model("features", "Feature")

    sport = Sport.objects.filter(name__iexact=SPORT_NAME).first() or Sport.objects.create(name=SPORT_NAME)
    features = {}
    for code, (name, description) in FEATURES.items():
        features[code], _created = Feature.objects.get_or_create(code=code, defaults={"name": name, "description": description, "kind": "flag", "is_active": True})

    for name, feature_code in COMPETITIONS.items():
        competition, created = Competition.objects.get_or_create(name=name, defaults={"module": MODULE, "sport": sport, "feature": features.get(feature_code)})
        if created:
            continue
        changed = []
        if competition.sport_id is None:
            competition.sport, changed = sport, [*changed, "sport"]
        if competition.feature_id is None and feature_code:
            competition.feature, changed = features[feature_code], [*changed, "feature"]
        if not competition.module:
            competition.module, changed = MODULE, [*changed, "module"]
        if changed:
            competition.save(update_fields=changed)


def unseed(apps, schema_editor):
    if getattr(schema_editor.connection, "schema_name", get_public_schema_name()) != get_public_schema_name():
        return
    Sport = apps.get_model("club", "Sport")
    Competition = apps.get_model("club", "Competition")
    Feature = apps.get_model("features", "Feature")
    PlanFeature = apps.get_model("features", "PlanFeature")
    FeatureOverride = apps.get_model("features", "FeatureOverride")

    Competition.objects.filter(name__in=list(COMPETITIONS), module__startswith=MODULE).delete()
    for feature in Feature.objects.filter(code__in=list(FEATURES)):
        if not (Competition.objects.filter(feature=feature).exists() or PlanFeature.objects.filter(feature=feature).exists() or FeatureOverride.objects.filter(feature=feature).exists()):
            feature.delete()
    for sport in Sport.objects.filter(name__iexact=SPORT_NAME):
        if not (sport.clubs.exists() or sport.competitions.exists() or sport.referee_types.exists()):
            sport.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("club", "0009_translatable_model_strings"),
        ("features", "0002_translatable_model_strings"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
