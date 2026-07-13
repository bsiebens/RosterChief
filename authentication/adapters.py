"""allauth adapters.

The MFA adapter exists for one important reason: WebAuthn credentials are bound
to a **Relying Party ID** (a domain). allauth's default RP ID is the request's
host — which under our subdomain tenancy would be ``ajax-united.clubmanager.app``,
binding a passkey to *one club*. A member of two clubs would then need two
passkeys, and a credential registered at one club would silently fail at another.

Pinning the RP ID to the registrable parent domain (``clubmanager.app``) makes a
single passkey work across every club subdomain.
"""

from allauth.mfa.adapter import DefaultMFAAdapter
from django.conf import settings


class ClubManagerMFAAdapter(DefaultMFAAdapter):
    def get_public_key_credential_rp_entity(self) -> dict[str, str]:
        return {
            "id": webauthn_rp_id(),
            "name": settings.MFA_WEBAUTHN_RP_NAME,
        }


def webauthn_rp_id() -> str:
    """The registrable parent domain that passkeys are bound to.

    Falls back to the request host when no base domain is configured (e.g. a
    bare ``localhost`` dev server), which keeps WebAuthn usable there.
    """
    base_domain = getattr(settings, "CLUBMANAGER_BASE_DOMAIN", "")
    if base_domain:
        return base_domain

    from allauth.core import context

    return context.request.get_host().partition(":")[0]
