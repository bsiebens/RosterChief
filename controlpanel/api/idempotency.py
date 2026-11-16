"""``Idempotency-Key`` support for write endpoints.

Apply ``@idempotent`` *under* the route decorator on a view that returns ``(status, payload)``. A repeated request with
the same key replays the stored result instead of acting twice.
"""

import functools
import hashlib

from django.db import IntegrityError, transaction
from pydantic_core import to_jsonable_python

from controlpanel.models import IdempotencyRecord

from .errors import ApiError

HEADER = "Idempotency-Key"


def idempotent(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        key = request.headers.get(HEADER)
        if not key:
            return view(request, *args, **kwargs)
        endpoint = f"{request.method} {request.path}"
        fingerprint = hashlib.sha256(endpoint.encode() + b"\0" + request.body).hexdigest()
        try:
            with transaction.atomic():
                record = IdempotencyRecord.objects.create(user=request.user, key=key[:128], endpoint=endpoint, fingerprint=fingerprint)
        except IntegrityError:
            record = IdempotencyRecord.objects.get(user=request.user, key=key[:128])
            if record.fingerprint != fingerprint:
                raise ApiError(422, "idempotency_key_reused", "That Idempotency-Key was used for a different request.") from None
            if record.state == IdempotencyRecord.DONE:
                return record.status_code, record.response
            raise ApiError(409, "request_in_progress", "A request with this Idempotency-Key is still being processed.") from None
        try:
            status, payload = view(request, *args, **kwargs)
        except BaseException:
            record.delete()  # failures release the key so the client can retry
            raise
        record.state, record.status_code, record.response = IdempotencyRecord.DONE, status, to_jsonable_python(payload)
        record.save(update_fields=["state", "status_code", "response", "modified"])
        return status, payload

    return wrapper
