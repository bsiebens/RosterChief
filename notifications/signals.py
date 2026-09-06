"""Generic fan-out signal for this app's own batch operations -- kept
separate from any specific delivery channel so a new channel (push, SMS,
...) can hook in without notifications needing to know it exists, mirroring
this app's own "reusable for other kinds of activity later" design (see
models.py's Notification docstring)."""

import django.dispatch

#: Sent once per notifications.services.notify_members() call, after every
#: Notification row in the batch has been created (and emailed, if
#: requested), with the created rows as ``notifications=[...]``. Deliberately
#: not a post_save-per-instance signal: a channel that wants to act on a
#: whole batch at once (e.g. resolving every member's PushSubscriptions in
#: one query, see mobile.services.push.send_push_for_notifications) gains
#: nothing from being told about each row individually, and notify_members
#: is the only place a Notification is ever created.
notifications_created = django.dispatch.Signal()
