"""Reminders about onboarding documents (inbox + push, category "reminder").

``send_due`` is the daily sweep for the current club schema. For every ACTIVE membership of the current season and every
active requirement it looks at the checklist item and, when one of these holds, tells the member -- or, for a child, their
guardians too (``members.services.recipients``), one message per account, in that account's language:

* ``expiring``: completed, with a ``valid_until`` within the requirement's ``reminder_days_before``;
* ``expired``: completed but ``valid_until`` has passed;
* ``missing``: nothing received yet.

Nobody is nagged: the kind of the last reminder and its time are kept on the status row. ``expiring`` goes out once per
completion; ``expired`` and ``missing`` are repeated at most every ``REPEAT_AFTER`` days. A new completion (or a reopen)
resets the cycle, see ``onboarding._resolve``."""

import datetime
import logging

from django.utils import timezone

from members.models import ClubMembership, MemberRequirementStatus
from members.services import onboarding, recipients

logger = logging.getLogger(__name__)

CATEGORY = "reminder"
DEFAULT_LANGUAGE = "nl"
#: ``expired`` / ``missing`` reminders are repeated after this many days of silence.
REPEAT_AFTER = datetime.timedelta(days=14)

TEXTS = {
    "nl": {
        "expiring_title": "Document verloopt binnenkort: {name}",
        "expiring_body": "{who}: “{name}” is geldig tot {date}. Bezorg de club tijdig een nieuw exemplaar.",
        "expired_title": "Document verlopen: {name}",
        "expired_body": "{who}: “{name}” was geldig tot {date}. Bezorg de club een nieuw exemplaar.",
        "missing_title": "Document ontbreekt: {name}",
        "missing_body": "{who}: de club heeft “{name}” nog niet ontvangen.",
        "q_expiring_title": "Antwoord verloopt binnenkort: {name}",
        "q_expiring_body": "{who}: je antwoord op “{name}” is geldig tot {date}. Bevestig of pas het tijdig aan.",
        "q_expired_title": "Antwoord verlopen: {name}",
        "q_expired_body": "{who}: je antwoord op “{name}” was geldig tot {date}. Beantwoord de vraag opnieuw.",
        "q_missing_title": "Vraag niet beantwoord: {name}",
        "q_missing_body": "{who}: de club wacht nog op je antwoord op “{name}”.",
        "self": "Jij",
    },
    "en": {
        "expiring_title": "Document expires soon: {name}",
        "expiring_body": "{who}: “{name}” is valid until {date}. Please give the club a new one in time.",
        "expired_title": "Document expired: {name}",
        "expired_body": "{who}: “{name}” was valid until {date}. Please give the club a new one.",
        "missing_title": "Document missing: {name}",
        "missing_body": "{who}: the club has not received “{name}” yet.",
        "q_expiring_title": "Answer expires soon: {name}",
        "q_expiring_body": "{who}: your answer to “{name}” is valid until {date}. Please confirm or update it in time.",
        "q_expired_title": "Answer expired: {name}",
        "q_expired_body": "{who}: your answer to “{name}” was valid until {date}. Please answer the question again.",
        "q_missing_title": "Question not answered: {name}",
        "q_missing_body": "{who}: the club is still waiting for your answer to “{name}”.",
        "self": "You",
    },
    "fr": {
        "expiring_title": "Document bientôt expiré : {name}",
        "expiring_body": "{who} : « {name} » est valable jusqu’au {date}. Merci d’en remettre un nouveau à temps.",
        "expired_title": "Document expiré : {name}",
        "expired_body": "{who} : « {name} » était valable jusqu’au {date}. Merci d’en remettre un nouveau.",
        "missing_title": "Document manquant : {name}",
        "missing_body": "{who} : le club n’a pas encore reçu « {name} ».",
        "q_expiring_title": "Réponse bientôt expirée : {name}",
        "q_expiring_body": "{who} : votre réponse à « {name} » est valable jusqu’au {date}. Merci de la confirmer ou de la mettre à jour à temps.",
        "q_expired_title": "Réponse expirée : {name}",
        "q_expired_body": "{who} : votre réponse à « {name} » était valable jusqu’au {date}. Merci de répondre à nouveau.",
        "q_missing_title": "Question sans réponse : {name}",
        "q_missing_body": "{who} : le club attend encore votre réponse à « {name} ».",
        "self": "Vous",
    },
}


def _lang(code: str | None) -> str:
    code = (code or "").split("-")[0].lower()
    return code if code in TEXTS else DEFAULT_LANGUAGE


def kind_for(item: onboarding.ChecklistItem) -> str | None:
    """What (if anything) this item warrants a reminder about."""
    return {"expiring_soon": "expiring", "expired": "expired", "missing": "missing"}.get(item.display)


def is_due(status: MemberRequirementStatus | None, kind: str, now: datetime.datetime) -> bool:
    """Whether a reminder of ``kind`` should go out now, given the last one recorded on ``status``."""
    if status is None or not status.last_reminder_at:
        return True
    if status.last_reminder_kind != kind:
        return True
    return kind != "expiring" and now - status.last_reminder_at >= REPEAT_AFTER


def _deliver(member, requirement, kind: str, valid_until, club) -> int:
    from members.models import Member
    from notifications import inbox

    rows = recipients.recipients(member)
    languages = dict(Member.objects.filter(user__in=[u for u, _c in rows]).values_list("user_id", "language"))
    told = 0
    for user, child in rows:
        texts = TEXTS[_lang(languages.get(user.pk))]
        who = texts["self"] if child is None else child.first_name
        fmt = {"name": requirement.name, "who": who, "date": f"{valid_until:%d/%m/%Y}" if valid_until else ""}
        prefix = "q_" if requirement.kind == requirement.Kind.QUESTION else ""  # "answer" instead of "provide"
        inbox.notify(user, club, title=texts[f"{prefix}{kind}_title"].format(**fmt), body=texts[f"{prefix}{kind}_body"].format(**fmt), kind=inbox.Kind.WARNING, href=f"/members/{member.pk}?tab=documents", push=True, category=CATEGORY)
        told += 1
    return told


def send_due(now: datetime.datetime | None = None) -> dict:
    """The sweep, in the current club schema. Returns ``{"reminders": n, "told": accounts}``."""
    from events.models import Season
    from events.services.attendance import current_club

    now = now or timezone.now()
    today = timezone.localtime(now).date()
    club, season = current_club(), Season.get_current(today)
    if club is None or season is None or not onboarding.open_requirements().exists():
        return {"reminders": 0, "told": 0}
    reminders = told = 0
    memberships = ClubMembership.objects.filter(season=season, status=ClubMembership.StatusChoices.ACTIVE).select_related("member__user").prefetch_related("requirement_statuses")
    for membership in memberships:
        statuses = {s.requirement_id: s for s in membership.requirement_statuses.all()}
        for requirement in onboarding.open_requirements():
            status = statuses.get(requirement.pk)
            item = onboarding.item_for(requirement, status, today)
            kind = kind_for(item)
            if kind is None or not is_due(status, kind, now):
                continue
            try:
                told += _deliver(membership.member, requirement, kind, item.valid_until, club)
            except Exception:  # one broken delivery must not stop the sweep
                logger.exception("Reminder for %s / %s failed", membership.member_id, requirement.pk)
                continue
            row, _created = MemberRequirementStatus.objects.get_or_create(membership=membership, requirement=requirement)
            MemberRequirementStatus.objects.filter(pk=row.pk).update(last_reminder_at=now, last_reminder_kind=kind)
            reminders += 1
    return {"reminders": reminders, "told": told}
