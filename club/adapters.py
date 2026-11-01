from allauth.account.adapter import DefaultAccountAdapter
from allauth.mfa.adapter import DefaultMFAAdapter
from django.conf import settings


class AccountAdapter(DefaultAccountAdapter):
    """Clubs invite users; open self-signup is disabled."""

    def is_open_for_signup(self, request):
        return False


class MFAAdapter(DefaultMFAAdapter):
    def get_public_key_credential_rp_entity(self):
        # Bind passkeys to the base domain so they work on every club subdomain.
        return {"id": settings.ROSTERCHIEF_BASE_DOMAIN, "name": "RosterChief"}
