"""Domain errors raised by ``members.services``. The API layer maps ``NotAllowed`` to 403 and ``Invalid`` to 422."""


class ServiceError(Exception):
    """Base class for every expected failure of a members service."""


class NotAllowed(ServiceError):
    """The actor may not do this (maps to 403)."""


class Invalid(ServiceError):
    """The request breaks a rule (maps to 422). ``errors`` optionally maps field -> list of messages."""

    def __init__(self, message: str = "", errors: dict | None = None, code: str = "invalid"):
        super().__init__(message)
        self.errors = errors or {}
        self.code = code
