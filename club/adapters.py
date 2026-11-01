from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.internal.httpkit import render_url
from allauth.headless.adapter import DefaultHeadlessAdapter
from allauth.mfa.adapter import DefaultMFAAdapter
from django.conf import settings
from django_tenants.utils import get_public_schema_name


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


class HeadlessAdapter(DefaultHeadlessAdapter):
    """Sends e-mailed links (verify e-mail, password reset) to the frontend app that fits where they were requested."""

    def frontend_app(self) -> str | None:
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            return None
        return "controlpanel" if tenant.schema_name == get_public_schema_name() else "member"

    def get_frontend_url(self, urlname: str, **kwargs) -> str | None:
        app = self.frontend_app()
        base, path = settings.FRONTEND_URLS.get(app or ""), settings.FRONTEND_PATHS.get(urlname)
        if not (base and path):
            return None  # app not deployed (yet): allauth falls back to its own /accounts/ page
        return render_url(self.request, base + path, **kwargs)
