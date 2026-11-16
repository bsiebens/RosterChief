"""Test helper: make the current club's plan include the ``evaluations`` feature."""

from contextlib import ExitStack, contextmanager
from unittest import mock

from features.entitlements import Entitlement, Entitlements


@contextmanager
def evaluations_feature():
    features = Entitlements({"evaluations": Entitlement(True)})
    with ExitStack() as stack:
        for target in ("features.middleware.entitlements_for_request", "features.context.get_current_entitlements", "features.backends.get_current_entitlements"):
            stack.enter_context(mock.patch(target, side_effect=lambda *args: features))
        yield
