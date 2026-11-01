from django.core.checks import Error, register


@register()
def permission_features_exist(app_configs, **kwargs):
    from authorization.catalog import CATALOG as PERMISSIONS

    from .catalog import CATALOG

    return [
        Error(f"Permission '{p.code}' references unknown feature '{p.feature}'.", id="features.E001")
        for p in PERMISSIONS.values()
        if p.feature and p.feature not in CATALOG
    ]
