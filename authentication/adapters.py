"""allauth adapters.

The MFA adapter exists for one important reason: WebAuthn credentials are bound
to a **Relying Party ID** (a domain). allauth's default RP ID is the request's
host — which under our subdomain tenancy would be ``ajax-united.rosterchief.app``,
binding a passkey to *one club*. A member of two clubs would then need two
passkeys, and a credential registered at one club would silently fail at another.

Pinning the RP ID to the registrable parent domain (``rosterchief.app``) makes a
single passkey work across every club subdomain.

The account adapter exists for a second, unrelated reason: routing allauth's own
mail (password reset, MFA/passkey notices, ...) through the same
rosterchief.mail.send_message choke point the rest of the app's automated email
already uses, so the control panel's "pause automated email" switch
(features.models.EmailSuppression) covers allauth's mail too -- except password
reset, which stays exempt.
"""

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core import context as allauth_context
from allauth.mfa.adapter import DefaultMFAAdapter
from django.conf import settings
from django.contrib.sites.shortcuts import get_current_site


class RosterChiefMFAAdapter(DefaultMFAAdapter):
    def get_public_key_credential_rp_entity(self) -> dict[str, str]:
        return {
            "id": webauthn_rp_id(),
            "name": settings.MFA_WEBAUTHN_RP_NAME,
        }


class RosterChiefAccountAdapter(DefaultAccountAdapter):
    """Same email-rendering steps as allauth's own DefaultAccountAdapter.send_mail
    -- there's no smaller seam to hook, since the stock method renders *and* sends
    in one call -- but the final send goes through rosterchief.mail.send_message
    instead of ``message.send()`` directly, exempted only for password reset:
    that has to keep working while the platform-wide switch is on, or there'd be
    no way back in for someone who forgot their password during the very window
    the switch is meant to cover, short of a database flip."""

    PASSWORD_RESET_TEMPLATE_PREFIX = "account/email/password_reset_key"

    def send_mail(self, template_prefix, email, context):
        from rosterchief.mail import send_message

        request = allauth_context.request
        ctx = {"request": request, "email": email, "current_site": get_current_site(request)}
        ctx.update(context)
        message = self.render_mail(template_prefix, email, ctx)
        send_message(message, exempt=(template_prefix == self.PASSWORD_RESET_TEMPLATE_PREFIX))


def webauthn_rp_id() -> str:
    """The registrable parent domain that passkeys are bound to.

    Falls back to the request host when no base domain is configured (e.g. a
    bare ``localhost`` dev server), which keeps WebAuthn usable there.
    """
    base_domain = getattr(settings, "ROSTERCHIEF_BASE_DOMAIN", "")
    if base_domain:
        return base_domain

    from allauth.core import context

    return context.request.get_host().partition(":")[0]
