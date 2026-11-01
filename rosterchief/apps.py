from django.contrib.admin.apps import AdminConfig


class RosterAdminConfig(AdminConfig):
    default_site = "rosterchief.admin.RosterAdminSite"
