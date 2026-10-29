"""``Idempotency-Key`` handling for writes a mobile network may retry (API.md §6).

Wrap an endpoint's work in ``idempotent``::

    @router.post("/shop/orders", response={201: OrderOut})
    def place(request, payload: OrderIn):
        return idempotent(request, lambda: (201, place_order(...)))

Without the header the work just runs. With it:

* the key is *claimed* before the work runs (a unique row per user and key), so two
  simultaneous requests can't both run it -- the second gets 409
  ``request_in_progress`` and retries;
* a successful (2xx) response is stored and replayed unchanged for ``TTL``;
* a failed attempt releases the key, so the client can retry with the same one;
* reusing a key for a different endpoint is 422 ``idempotency_key_reused``.
"""

import datetime
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from api.core.errors import ApiError
from api.models import IdempotencyRecord

HEADER = "Idempotency-Key"
TTL = datetime.timedelta(hours=24)
MAX_KEY_LENGTH = 128
#: status_code of a claimed key whose work hasn't finished yet.
IN_PROGRESS = 0


def _jsonable(body):
    """Round-trip through JSON so the stored body is exactly what was sent (UUIDs,
    dates and Decimals as strings, schema objects as dicts)."""
    if hasattr(body, "model_dump"):
        body = body.model_dump(mode="json")
    return json.loads(json.dumps(body, cls=DjangoJSONEncoder))


def idempotent(request, work):
    """Run ``work()`` -- which returns ``(status, body)`` -- at most once per key."""
    key = request.headers.get(HEADER, "").strip()
    if not key:
        return work()
    if len(key) > MAX_KEY_LENGTH:
        raise ApiError(422, "validation_error", fields={HEADER: [_("At most %(count)d characters.") % {"count": MAX_KEY_LENGTH}]})

    user = request.user
    IdempotencyRecord.objects.filter(user=user, created__lt=timezone.now() - TTL).delete()

    try:
        with transaction.atomic():
            record = IdempotencyRecord.objects.create(user=user, key=key, method=request.method, path=request.path, status_code=IN_PROGRESS)
    except IntegrityError:
        return _replay(request, IdempotencyRecord.objects.get(user=user, key=key))

    try:
        status, body = work()
    except BaseException:
        record.delete()
        raise
    if not 200 <= status < 300:
        record.delete()
        return status, body
    record.status_code = status
    record.body = _jsonable(body)
    record.save(update_fields=["status_code", "body", "modified"])
    return status, body


def _replay(request, record):
    if record.method != request.method or record.path != request.path:
        raise ApiError(422, "idempotency_key_reused", _("This Idempotency-Key was already used for a different request."))
    if record.status_code == IN_PROGRESS:
        raise ApiError(409, "request_in_progress", _("This request is still being processed. Try again in a moment."))
    return record.status_code, record.body
