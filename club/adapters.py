from allauth.account.adapter import DefaultAccountAdapter
from allauth.mfa.adapter import DefaultMFAAdapter
from django.conf import settings


class AccountAdapter(DefaultAccountAdapter):
    """Clubs invite users; open self-signup is disabled."""

    def send_mail(self, template_prefix, email, context):
        # Account email (password reset, invites, verification) is exempt from the platform's email pause.
        from rosterchief.mail import email_paused

        if email_paused() and not template_prefix.startswith("account/email/"):
            return
        super().send_mail(template_prefix, email, context)

    def is_open_for_signup(self, request):
        return False


class MFAAdapter(DefaultMFAAdapter):
    def get_public_key_credential_rp_entity(self):
        # Bind passkeys to the base domain so they work on every club subdomain.
        return {"id": settings.ROSTERCHIEF_BASE_DOMAIN, "name": "RosterChief"}
