"""Web Push delivery -- the push channel for notifications.Notification.

Wired in from mobile.signals (a post_save on Notification), deliberately kept
out of the notifications app itself, which stays channel-agnostic (see that
app's models.py docstring: "the whole point is reusing this for other kinds
of activity later"). A backward dependency the other way -- notifications
importing mobile -- would be the wrong direction: notifications has no
reason to know a PWA exists.
"""

import json
import logging

from django.conf import settings
from pywebpush import WebPushException, webpush

from .. import models

logger = logging.getLogger(__name__)


def send_push_to_member(member, *, title: str, body: str, url: str = "/app/") -> None:
    if not settings.VAPID_PRIVATE_KEY:
        # Dev/no-config default -- see the settings.py comment next to VAPID_PRIVATE_KEY.
        return

    # str(): title/body often arrive as a gettext_lazy proxy (or the still-lazy
    # result of formatting one, e.g. `_("...") % {...}` -- Django's lazy strings
    # stay lazy through `%` too), not a plain str -- json.dumps can't serialize
    # that object directly and raises TypeError. Forcing it here, once, at the
    # one place this payload actually gets encoded, is simpler and safer than
    # auditing every caller across the app that builds a title/body.
    payload = json.dumps({"title": str(title), "body": str(body), "url": url})
    for subscription in models.PushSubscription.objects.filter(member=member):
        try:
            webpush(
                subscription_info=subscription.as_subscription_info(),
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{settings.VAPID_ADMIN_EMAIL}"},
            )
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (404, 410):
                # The browser's push service says this registration is gone for good --
                # not a transient failure, so keeping it around would only mean retrying
                # a subscription that will never accept a push again.
                subscription.delete()
            else:
                logger.warning("Push send failed for %s: %s", member, exc)
        except OSError as exc:
            # Never fatal -- same reasoning as every other branded send in this app
            # (see e.g. notifications.services._send_email).
            logger.warning("Push send failed for %s: %s", member, exc)
