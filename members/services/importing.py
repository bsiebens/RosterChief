"""Mass import of members from a CSV or Excel file: parse, validate into a preview, then commit.

The preview is stateless: ``preview`` returns the normalised rows, the caller sends them back to ``commit``, which
validates them again (so a stale or edited preview can never write anything the rules forbid).

Per row, one of three outcomes: ``new`` (will be created), ``duplicate`` (an existing member has the same email, or the
same name and date of birth, or an earlier row in the file does; never changed or touched) or ``error`` (with messages
per column). Besides the member, a row may create a membership for the chosen season, a roster spot (``team``
column, needs the right to manage that team) and a family link (``family`` and ``family_role`` columns; rows with the
same family name end up in one family, in an existing family of that name when there is one).
"""

import csv
import datetime
import io

import phonenumbers
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from events.models import Season
from members.models import Family, FamilyMembership, Member
from members.services import audit, families, memberships
from members.services import members as member_services
from members.services.access import can_manage_team, require_admin
from members.services.errors import Invalid
from teams.models import Team
from teams.services import teams as team_services

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 2000
PHONE_REGION = "BE"  # numbers without a country code are read as Belgian

COLUMNS = ("first_name", "last_name", "email", "phone", "emergency_phone", "date_of_birth", "language", "license_number", "team", "family", "family_role")
REQUIRED = ("first_name", "last_name")

_ALIASES = {
    "first_name": ("first name", "firstname", "given name", "voornaam", "prenom", "prénom"),
    "last_name": ("last name", "lastname", "surname", "family name", "achternaam", "naam", "nom"),
    "email": ("e-mail", "email address", "mail"),
    "phone": ("telephone", "tel", "mobile", "gsm", "phone number", "telefoon"),
    "emergency_phone": ("emergency phone", "emergency", "emergency contact", "noodnummer", "urgence"),
    "date_of_birth": ("date of birth", "dob", "birth date", "birthdate", "birthday", "geboortedatum", "date de naissance"),
    "language": ("lang", "taal", "langue"),
    "license_number": ("license", "licence", "license number", "licence number", "licentie", "licentienummer"),
    "team": ("ploeg", "équipe", "equipe"),
    "family": ("familie", "famille"),
    "family_role": ("family role", "role in family", "familierol", "rôle"),
}
_LOOKUP = {a: col for col, aliases in _ALIASES.items() for a in (col, col.replace("_", " "), *aliases)}

TEMPLATE_ROWS = [
    ["Jane", "Doe", "jane.doe@example.com", "+32 470 12 34 56", "", "2014-03-01", "en", "LIC-1001", "U12", "Doe", "child"],
    ["John", "Doe", "john.doe@example.com", "0470 98 76 54", "", "1980-06-15", "nl", "", "", "Doe", "parent"],
]


class FileError(Invalid):
    """The file itself is unusable (format, size, headers); reported against the ``file`` field."""

    def __init__(self, message: str):
        super().__init__(message, {"file": [message]})


# -- reading the file --


def _norm_header(value) -> str | None:
    key = " ".join(str(value or "").strip().lower().replace("_", " ").split())
    return _LOOKUP.get(key) or _LOOKUP.get(key.replace(" ", "_"))


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_csv(data: bytes) -> list[list]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return list(csv.reader(io.StringIO(text), dialect))


def _read_xlsx(data: bytes) -> list[list]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise FileError("This is not a valid Excel file.") from e
    sheet = wb.worksheets[0] if wb.worksheets else None
    return [list(r) for r in sheet.iter_rows(values_only=True)] if sheet is not None else []


def parse_file(upload) -> list[dict]:
    """The rows of an uploaded ``.csv`` or ``.xlsx`` as dicts keyed by our column names (all values strings), each with
    its ``line`` number in the file. Unknown columns are ignored."""
    name = (getattr(upload, "name", "") or "").lower()
    data = upload.read()
    if len(data) > MAX_BYTES:
        raise FileError("The file is larger than 5 MB.")
    if name.endswith(".xlsx"):
        grid = _read_xlsx(data)
    elif name.endswith((".csv", ".txt")):
        grid = _read_csv(data)
    else:
        raise FileError("Upload a .csv or .xlsx file.")
    grid = [r for r in grid if any(_cell(c) for c in r)]
    if not grid:
        raise FileError("The file is empty.")
    columns = [_norm_header(h) for h in grid[0]]
    missing = [c for c in REQUIRED if c not in columns]
    if missing:
        raise FileError(f"Missing column(s): {', '.join(missing)}. The first row must hold the column names.")
    body = grid[1:]
    if not body:
        raise FileError("The file has a header but no rows.")
    if len(body) > MAX_ROWS:
        raise FileError(f"A file can hold at most {MAX_ROWS} rows.")
    rows = []
    for i, raw in enumerate(body, start=2):
        row = dict.fromkeys(COLUMNS, "")
        for col, value in zip(columns, raw, strict=False):
            if col and not row[col]:
                row[col] = _cell(value)
        row["line"] = i
        rows.append(row)
    return rows


def template_csv() -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(COLUMNS)
    w.writerows(TEMPLATE_ROWS)
    return out.getvalue()


# -- validating --


def _date(value: str) -> datetime.date | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(value)


def _phone(value: str) -> str:
    parsed = phonenumbers.parse(value, PHONE_REGION)
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError(value)
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _age_under_18(dob: datetime.date | None) -> bool:
    if dob is None:
        return False
    today = datetime.date.today()
    return (today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))) < 18


def _clean_row(row: dict, *, season, actor, teams: dict) -> tuple[dict, dict]:
    """``(normalised values, errors)`` for one row; values hold strings only (dates as ISO)."""
    v = {c: str(row.get(c) or "").strip() for c in COLUMNS}
    errors: dict[str, list[str]] = {}

    def err(field, message):
        errors.setdefault(field, []).append(message)

    for f in REQUIRED:
        if not v[f]:
            err(f, "Required.")
    if v["email"]:
        try:
            validate_email(v["email"])
        except ValidationError:
            err("email", "Enter a valid email address.")
    dob = None
    if v["date_of_birth"]:
        try:
            dob = _date(v["date_of_birth"])
            v["date_of_birth"] = dob.isoformat()
            if dob > datetime.date.today():
                err("date_of_birth", "Can't be in the future.")
        except ValueError:
            err("date_of_birth", "Use a date like 2014-03-01 or 01/03/2014.")
    for f in ("phone", "emergency_phone"):
        if v[f]:
            try:
                v[f] = _phone(v[f])
            except Exception:
                err(f, "Not a valid phone number (include the country code for non-Belgian numbers).")
    if v["language"]:
        code = v["language"].lower()[:2]
        if code not in {c for c, _ in settings.LANGUAGES}:
            err("language", f"Use one of: {', '.join(c for c, _ in settings.LANGUAGES)}.")
        else:
            v["language"] = code
    if v["team"]:
        team = teams.get(v["team"].casefold())
        if season is None:
            err("team", "Choose a season to place players on a team.")
        elif team is None:
            err("team", f"Unknown team '{v['team']}'.")
        elif not can_manage_team(actor, team):
            err("team", f"You are not allowed to add players to {team.name}.")
        else:
            v["team"] = team.name
    if v["family_role"]:
        role = v["family_role"].casefold()
        if role not in {r.value for r in FamilyMembership.FamilyRole}:
            err("family_role", f"Use one of: {', '.join(r.value for r in FamilyMembership.FamilyRole)}.")
        else:
            v["family_role"] = role
    if v["family"] and not v["family_role"]:
        v["family_role"] = FamilyMembership.FamilyRole.CHILD if _age_under_18(dob) else FamilyMembership.FamilyRole.PARENT
    elif v["family_role"] and not v["family"]:
        err("family", "Needed when a family role is given.")
    return v, errors


def _team_lookup() -> dict:
    out = {}
    for t in Team.objects.all():
        out[t.name.casefold()] = t
        out.setdefault(t.short_name.casefold(), t)
    return out


def validate_rows(actor, rows: list[dict], season) -> list[dict]:
    """One result per row: the normalised values plus ``status`` (new / duplicate / error), ``errors`` and a ``note``."""
    require_admin(actor)
    teams = _team_lookup()
    emails = {e.lower() for e in Member.objects.exclude(email="").values_list("email", flat=True)}
    names = {(f.lower(), l.lower(), d) for f, l, d in Member.objects.exclude(date_of_birth=None).values_list("first_name", "last_name", "date_of_birth")}
    seen_emails: set[str] = set()
    seen_names: set = set()
    out = []
    for row in rows:
        values, errors = _clean_row(row, season=season, actor=actor, teams=teams)
        result = {**values, "line": int(row.get("line") or 0), "errors": errors, "status": "error", "note": ""}
        if not errors:
            email = values["email"].lower()
            dob = datetime.date.fromisoformat(values["date_of_birth"]) if values["date_of_birth"] else None
            key = (values["first_name"].lower(), values["last_name"].lower(), dob)
            if (email and email in emails) or (dob and key in names):
                result.update(status="duplicate", note="Already a member.")
            elif (email and email in seen_emails) or (dob and key in seen_names):
                result.update(status="duplicate", note="Appears earlier in this file.")
            else:
                result["status"] = "new"
            if email:
                seen_emails.add(email)
            if dob:
                seen_names.add(key)
        out.append(result)
    return out


def summary(results: list[dict]) -> dict:
    return {s: sum(1 for r in results if r["status"] == s) for s in ("new", "duplicate", "error")}


def get_season(season_id):
    if season_id is None:
        return None
    season = Season.objects.filter(pk=season_id).first()
    if season is None:
        raise Invalid("Unknown season.", {"season_id": ["Unknown season."]})
    return season


def preview(actor, upload, season_id=None) -> dict:
    require_admin(actor)
    season = get_season(season_id)
    results = validate_rows(actor, parse_file(upload), season)
    return {"season_id": season.pk if season else None, "counts": summary(results), "rows": results}


# -- committing --


def start_import(actor, rows: list[dict], season_id=None):
    """Queue a background import (``members.tasks.run_member_import``) and return its ``ImportJob``. The rows are
    validated again when the job runs; only the rows that previewed as new need to be sent."""
    from members.models import ImportJob

    require_admin(actor)
    if not rows:
        raise Invalid("There is nothing to import.", {"rows": ["There is nothing to import."]})
    if len(rows) > MAX_ROWS:
        raise Invalid(f"At most {MAX_ROWS} rows.", {"rows": [f"At most {MAX_ROWS} rows."]})
    get_season(season_id)
    job = ImportJob.objects.create(created_by=actor, season_id=season_id, rows=rows, total=len(rows))
    from members.tasks import run_member_import

    transaction.on_commit(lambda: run_member_import.delay(str(job.pk)))
    return job


@transaction.atomic
def commit(actor, club, rows: list[dict], season_id=None, *, features=None, progress=None) -> dict:
    """Create the rows that validate as ``new``; skip duplicates and report errors. A plan-limit error
    (``LimitExceeded``) aborts the whole import, nothing is saved. ``progress(done, total)`` is called after each row."""
    require_admin(actor)
    if len(rows) > MAX_ROWS:
        raise Invalid(f"At most {MAX_ROWS} rows.", {"rows": [f"At most {MAX_ROWS} rows."]})
    season = get_season(season_id)
    results = validate_rows(actor, rows, season)
    teams = _team_lookup()
    created: list[tuple[dict, Member]] = []
    total = len(results)
    for index, r in enumerate(results, start=1):
        if progress is not None:
            progress(index - 1, total)
        if r["status"] != "new":
            continue
        try:
            with transaction.atomic():
                fields = {k: r[k] or None for k in ("phone", "emergency_phone", "license_number")}
                fields = {k: v for k, v in fields.items() if v}
                if r["date_of_birth"]:
                    fields["date_of_birth"] = datetime.date.fromisoformat(r["date_of_birth"])
                if r["language"]:
                    fields["language"] = r["language"]
                member = member_services.create_member(actor, club, first_name=r["first_name"], last_name=r["last_name"], email=r["email"], features=features, **fields)
                if season is not None:
                    memberships.create_membership(actor, member, season)
                if r["team"]:
                    team_services.add_player(actor, teams[r["team"].casefold()], member_id=member.pk, season_id=season.pk, position_id=None, jersey_number=None, is_captain=False, is_alternate_captain=False, check_membership=False)
            created.append((r, member))
            r["status"] = "created"
        except Invalid as e:
            r["status"], r["errors"] = "error", e.errors or {"__all__": [str(e)]}
    if progress is not None:
        progress(total, total)
    _link_families(actor, created)
    counts = {"created": len(created), "duplicate": sum(1 for r in results if r["status"] == "duplicate"), "error": sum(1 for r in results if r["status"] == "error")}
    audit.record(actor, "club.members_imported", changes={k: {"from": None, "to": v} for k, v in {**counts, "season": season.name if season else None}.items()})
    return {"counts": counts, "rows": results}


def _link_families(actor, created: list[tuple[dict, Member]]) -> None:
    """Rows sharing a family name end up in one family; an existing family with that name is reused."""
    groups: dict[str, list[tuple[Member, str]]] = {}
    labels: dict[str, str] = {}
    for r, member in created:
        if r["family"]:
            key = r["family"].casefold()
            labels.setdefault(key, r["family"])
            groups.setdefault(key, []).append((member, r["family_role"]))
    for key, people in groups.items():
        existing = Family.objects.filter(name__iexact=labels[key]).first()
        if existing is None:
            families.create_family(actor, name=labels[key], members=people)
        else:
            for member, role in people:
                families.add_member(actor, existing, member, role)
