"""Weekly re-check of every team's stored fixture import sources (Team.import_sources).

For each active club, takes its *current* season's stored entries (see
events.services.import_sources), skips any whose source the club no longer has turned on,
and runs ``check_entry`` -- the same diff the one-off import previews, narrowed to what's
worth a human's attention: a game moved to another date/hour, a new game, a removed future
game, or a fixture list that couldn't be fetched at all. Never imports anything itself:
it records the outcome on the entry (``last_checked_at``/``last_check``) and, when any entry
in a club has changes, sends that club's admins ONE notification summarising them, pointing
them at the re-check page to review and apply via the import button.

Read-only towards events, so no --commit gate (same reasoning as extend_event_series being
additive): the worst a bad run can do is send an unnecessary heads-up.
"""

import logging

from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from club.models import Club, ClubRole, Season
from events.services.import_sources import CheckResult, check_entry, source_enabled, sources_for, update_entry
from features.commands import ScheduledJobCommand
from members.models import Member
from notifications.models import NotificationCategory
from notifications.services import notify_members
from teams.models import Team

logger = logging.getLogger(__name__)


def club_admins(club):
    """Every member holding the ADMIN role in ``club`` -- the same "club admins" the
    billing reminders write to (billing.services.reminders.admin_emails), as Members so
    notify_members can record an in-app row for each of them too."""
    return Member.objects.filter(roles__club=club, roles__role=ClubRole.Roles.ADMIN).distinct().order_by("last_name", "first_name")


def describe_result(team, result: CheckResult) -> str:
    """One summary line for one entry's check, e.g. "First Team · RBIHF · Division 1:
    2 games moved to another date or time, 1 new game." """
    entry = result.entry
    label = (entry.get("competition_label") or "").strip()
    if label:
        heading = _("%(team)s · %(source)s · %(competition)s") % {"team": team.name, "source": entry.get("source", ""), "competition": label}
    else:
        heading = _("%(team)s · %(source)s") % {"team": team.name, "source": entry.get("source", "")}

    if result.error:
        return _("%(heading)s: the fixture list could not be fetched (%(error)s).") % {"heading": heading, "error": result.error}

    parts = []
    if result.rescheduled:
        count = len(result.rescheduled)
        parts.append(ngettext("%(count)d game moved to another date or time", "%(count)d games moved to another date or time", count) % {"count": count})
    if result.new_games:
        count = len(result.new_games)
        parts.append(ngettext("%(count)d new game", "%(count)d new games", count) % {"count": count})
    if result.removed_games:
        count = len(result.removed_games)
        parts.append(ngettext("%(count)d game removed", "%(count)d games removed", count) % {"count": count})
    return _("%(heading)s: %(changes)s.") % {"heading": heading, "changes": _(", ").join(parts)}


def build_body(lines: list[str]) -> str:
    intro = _("This week's check of your imported fixture lists found differences with the federation's current schedule:")
    outro = _("Open the fixture re-check page in management, re-run the check and adjust the games via the import button.")
    bullets = "\n".join(f"- {line}" for line in lines)
    return f"{intro}\n\n{bullets}\n\n{outro}"


class Command(ScheduledJobCommand):
    help = "Re-check every team's stored fixture import sources for the current season and notify club admins of moved, new or removed games."
    job_name = "events.tasks.check_fixture_sources"

    def handle(self, *args, **options):
        today = timezone.localdate()
        checked = changed = errors = clubs_notified = 0

        for club in Club.objects.active().order_by("name"):
            season = Season.covering(club, today)
            if season is None:
                continue

            enabled = {}
            changes = []  # (team, result): rendered per language when the notification is sent
            for team in Team.objects.filter(club=club).order_by("name"):
                for entry in sources_for(team, season):
                    source = entry["source"]
                    if source not in enabled:
                        enabled[source] = source_enabled(club, source)
                    if not enabled[source]:
                        continue

                    result = self.check(club, team, entry)
                    checked += 1
                    if result.error:
                        errors += 1
                    try:
                        update_entry(team, entry, last_checked_at=timezone.now().isoformat(), last_check=result.as_dict())
                    except Exception:
                        logger.exception("check_fixture_sources.bookkeeping_failed club=%s team=%s source=%s", club.pk, team.pk, source)
                    if result.has_changes:
                        changed += 1
                        changes.append((team, result))

            if changes and self.notify(club, changes):
                clubs_notified += 1

        summary = f"Checked {checked} fixture source(s): {changed} with changes ({errors} could not be fetched); notified {clubs_notified} club(s)."
        self.stdout.write(self.style.SUCCESS(summary))
        return summary

    def check(self, club, team, entry) -> CheckResult:
        """check_entry already reports fetch/parse problems on the result; anything else
        unexpected is caught here too, so one bad entry never stops the sweep."""
        try:
            return check_entry(club, team, entry)
        except Exception as error:
            logger.exception("check_fixture_sources.entry_failed club=%s team=%s source=%s", club.pk, team.pk, entry.get("source"))
            self.stdout.write(self.style.ERROR(f"{club} / {team} / {entry.get('source')}: {error}"))
            return CheckResult(entry=entry, error=_("Unexpected error while checking this fixture list."))

    def notify(self, club, changes: list) -> bool:
        admins = list(club_admins(club))
        if not admins:
            self.stdout.write(self.style.WARNING(f"{club}: fixture changes found but the club has no admins to notify."))
            return False

        def title():
            return _("Fixture changes found for %(club)s") % {"club": club.name}

        def body():
            return build_body([describe_result(team, result) for team, result in changes])

        notify_members(admins, club=club, title=title, body=body, category=NotificationCategory.ADMIN)
        self.stdout.write(f"{club}: notified {len(admins)} admin(s) about {len(changes)} changed fixture source(s).")
        return True
