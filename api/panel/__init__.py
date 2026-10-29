"""Management-panel routes of the API (protected, authorised per club role).

Empty on purpose: see docs/API.md "Management API". New routers live here, are listed in
``api.v1.PROTECTED`` and reuse ``api.core`` (``club_scope``, ``paginate``, ``ApiError``,
``idempotent``). They are the JSON counterpart of the ``management`` app's views.
"""
