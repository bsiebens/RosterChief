"""The client's IP address as seen through the reverse proxy.

In every deployment, Caddy sits in front of gunicorn and sets ``X-Real-IP`` to
the visitor's address (``header_up X-Real-IP {client_ip}``, which replaces any
client-supplied value; behind Cloudflare, ``{client_ip}`` comes from
CF-Connecting-IP only for connections from Cloudflare's own ranges -- see
deploy/caddy/Caddyfile), so ``REMOTE_ADDR`` is always the proxy's own address. The header named by ``ALLAUTH_TRUSTED_CLIENT_IP_HEADER`` is read
first; ``REMOTE_ADDR`` is the fallback for requests that never passed through
the proxy (``runserver``, the test client, a container healthcheck).
"""

import ipaddress

from django.conf import settings


def _valid_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request) -> str | None:
    header = getattr(settings, "ALLAUTH_TRUSTED_CLIENT_IP_HEADER", None)
    if header:
        ip = _valid_ip(request.headers.get(header))
        if ip:
            return ip
    return _valid_ip(request.META.get("REMOTE_ADDR"))
