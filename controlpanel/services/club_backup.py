"""Portable club backups. Every record is scoped to one tenant, including shared references."""

import json
import logging
import re
import shutil
from collections import defaultdict
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from tempfile import SpooledTemporaryFile
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile
from zlib import error as CompressionError

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.management.color import no_style
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import validate_email
from django.db import DatabaseError, connection, models, transaction
from django.utils import timezone
from django.utils.text import get_valid_filename, slugify
from django_tenants.utils import get_public_schema_name, schema_context
from PIL import Image, UnidentifiedImageError

from authorization.catalog import CATALOG as PERMISSION_CATALOG
from authorization.models import Role
from authorization.services import assign_role, create_role, set_role_perms, sync_permissions
from club import services as clubs
from club.models import Club, RefereeType, Sport, User
from features.catalog import CATALOG as FEATURE_CATALOG
from features.entitlements import resolve_entitlements
from features.models import Feature, FeatureOverride

FORMAT = "rosterchief-club-backup"
MAX_UPLOAD = 512 * 1024**2
MAX_EXPANDED = 2 * 1024**3
MAX_MANIFEST = 32 * 1024**2
logger = logging.getLogger(__name__)

# Restrict serialization to club applications. Explicitly skip derived data and transient jobs.
TENANT_APPS = {"members", "sponsoring", "teams", "events", "news", "formbuilder", "shop", "evaluations", "registration"}
EXCLUDED_MODELS = {"members.importjob", "events.gamestat", "events.gamemoment", "events.statsrefresh"}
EXCLUDED_FIELDS = {"shop.shopsettings": {"credentials_encrypted"}}
CLUB_FIELDS = {
    "name", "legal_name", "contact_email", "website", "legal_address", "legal_zip_code", "legal_city", "legal_country",
    "vat_id", "payment_instructions", "logo", "event_background", "primary_color", "secondary_color", "primary_text_color",
    "secondary_text_color", "shop_enabled", "season_start", "season_duration_months",
}
IMAGE_EXTENSIONS = {"AVIF": ".avif", "GIF": ".gif", "JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp", "TIFF": ".tif"}
SVG_NAMESPACE = "{http://www.w3.org/2000/svg}"
SVG_ELEMENTS = {
    "svg", "g", "defs", "path", "line", "rect", "circle", "ellipse", "polyline", "polygon", "title", "desc",
    "symbol", "use", "linearGradient", "radialGradient", "stop", "pattern", "clipPath", "mask", "text", "tspan",
}
SVG_ATTRIBUTES = {
    "id", "class", "style", "version", "width", "height", "viewBox", "preserveAspectRatio", "transform",
    "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "d", "points", "dx", "dy",
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-linecap",
    "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset", "opacity", "color",
    "offset", "stop-color", "stop-opacity", "gradientUnits", "gradientTransform", "spreadMethod", "fx", "fy",
    "clip-path", "clip-rule", "clipPathUnits", "mask", "maskUnits", "maskContentUnits", "patternUnits",
    "patternContentUnits", "patternTransform", "display", "visibility", "font-family", "font-size", "font-weight",
    "text-anchor", "dominant-baseline",
}
SVG_HREF = {"href", "{http://www.w3.org/1999/xlink}href"}
SVG_FRAGMENT = r"#[a-zA-Z_][\w.-]*"


class BackupError(ValueError):
    """A backup cannot be exported or safely restored."""

    def __init__(self, message, *, field=None, report=None):
        super().__init__(message)
        self.field = field
        self.report = report


def tenant_models():
    return {
        model._meta.label_lower: model
        for model in apps.get_models()
        if model._meta.app_label in TENANT_APPS and model._meta.label_lower not in EXCLUDED_MODELS
    }


def _fields(model):
    excluded = EXCLUDED_FIELDS.get(model._meta.label_lower, set())
    return {field.attname: field for field in model._meta.concrete_fields if field.name not in excluded}


def _many(model):
    return {field.name: field for field in model._meta.many_to_many if field.remote_field.through._meta.auto_created}


def _permission_codes(permissions):
    return sorted(
        code for app, codename in permissions.values_list("content_type__app_label", "codename")
        if (code := f"{app}.{codename}") in PERMISSION_CATALOG
    )


class _Export:
    def __init__(self, archive):
        self.archive = archive
        self.files = {}
        self.references = defaultdict(set)
        self.expanded_files = 0

    def file(self, field_file):
        if not field_file:
            return ""
        key = (id(field_file.storage), field_file.name)
        if key in self.files:
            return self.files[key]
        member = f"media/{len(self.files) + 1}/{get_valid_filename(PurePosixPath(field_file.name).name)}"
        try:
            self.expanded_files += field_file.size
            if self.expanded_files > MAX_EXPANDED:
                raise BackupError("This club exceeds the size limits of a portable backup.")
            with field_file.open("rb") as source, self.archive.open(member, "w", force_zip64=True) as destination:
                shutil.copyfileobj(source, destination)
        except (FileNotFoundError, OSError) as error:
            raise BackupError(f"A referenced file is missing or unreadable: {field_file.name}") from error
        self.files[key] = member
        return member

    def fields(self, obj, fields):
        result = {}
        for name, field in fields.items():
            value = field.value_from_object(obj)
            if isinstance(field, models.FileField):
                result[name] = self.file(value)
            elif field.is_relation:
                result[name] = str(value) if value is not None else None
                if value is not None:
                    self.references[field.related_model].add(value)
            elif value is None or isinstance(field, (models.JSONField, models.BooleanField, models.IntegerField, models.FloatField)):
                result[name] = value
            else:
                result[name] = field.value_to_string(obj)
        return result

    def rows(self, model):
        fields = _fields(model)
        many = defaultdict(dict)
        for name, field in _many(model).items():
            through = field.remote_field.through
            source = f"{field.m2m_field_name()}_id"
            target = f"{field.m2m_reverse_field_name()}_id"
            for parent, child in through._base_manager.values_list(source, target).iterator():
                many[parent].setdefault(name, []).append(str(child))
        return [
            {"fields": self.fields(obj, fields), "many": many.get(obj.pk, {})}
            for obj in model._base_manager.order_by("pk").iterator()
        ]


def export_club(club):
    """Return a disk-spooled ZIP ready for a streaming response."""
    spool = SpooledTemporaryFile(max_size=8 * 1024**2, mode="w+b")
    try:
        outer_transaction = connection.in_atomic_block
        with transaction.atomic(), ZipFile(spool, "w", compression=ZIP_DEFLATED) as archive:
            if not outer_transaction:
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            exporter = _Export(archive)
            with schema_context(club.schema_name):
                records = {label: exporter.rows(model) for label, model in tenant_models().items()}
                roles = [
                    {"slug": role.slug, "name": role.group.name, "description": role.description,
                     "permissions": _permission_codes(role.group.permissions)}
                    for role in Role.objects.select_related("group").order_by("slug")
                ]
                accounts = []
                club_users = list(club.user_set.all())
                enrolled = {user.pk for user in club_users}
                historic = User.objects.filter(pk__in=exporter.references[User] - enrolled)
                role_by_group = dict(Role.objects.values_list("group_id", "slug"))
                for user in [*club_users, *historic]:
                    perms = user.tenant_perms if user.pk in enrolled else None
                    accounts.append({
                        "id": str(user.pk), "email": user.email, "first_name": user.first_name, "last_name": user.last_name,
                        "active": user.is_active, "enrolled": perms is not None,
                        "roles": [role_by_group[group] for group in perms.groups.values_list("pk", flat=True) if group in role_by_group] if perms else [],
                        "permissions": _permission_codes(perms.user_permissions) if perms else [],
                        "staff": bool(perms and perms.is_staff), "superuser": bool(perms and perms.is_superuser),
                    })
            with schema_context(get_public_schema_name()):
                fields = exporter.fields(club, {name: field for name, field in _fields(Club).items() if name in CLUB_FIELDS})
                entitlements = resolve_entitlements(club)
                types = RefereeType.objects.filter(pk__in=exporter.references[RefereeType]).select_related("sport")
                manifest = {
                    "format": FORMAT, "version": 2, "exported_at": timezone.now(),
                    "club": {"slug": club.slug, "owner": str(club.owner_id), "fields": fields, "sport": _sport(club.sport)},
                    "accounts": accounts, "roles": roles, "records": records,
                    "referee_types": {str(item.pk): {"sport": _sport(item.sport), "kind": item.kind, "name": item.name, "ordering": item.ordering} for item in types},
                    "features": {
                        code: {"enabled": entitlements.has(code), "limit": entitlements.limit(code)}
                        for code in FEATURE_CATALOG
                    },
                }
                archive.writestr("manifest.json", json.dumps(manifest, cls=DjangoJSONEncoder, ensure_ascii=False, separators=(",", ":")))
            expanded = sum(item.file_size for item in archive.infolist())
            if expanded > MAX_EXPANDED or archive.getinfo("manifest.json").file_size > MAX_MANIFEST:
                raise BackupError("This club exceeds the size limits of a portable backup.")
        if spool.tell() > MAX_UPLOAD:
            raise BackupError("This club exceeds the size limits of a portable backup.")
    except BaseException:
        spool.close()
        raise
    spool.seek(0)
    return spool


def _sport(sport):
    return {"slug": sport.slug, "name": sport.name} if sport else None


_SVG_NAMESPACES = {"": "http://www.w3.org/2000/svg", "xlink": "http://www.w3.org/1999/xlink"}
_SVG_UNSAFE_CSS = re.compile(r"@|expression|javascript|behavio|\\|url\(\s*['\"]?(?!#)", re.I)


def _static_svg(source):
    """A legacy SVG made inert: everything active or external is stripped, what is drawn stays.

    Kept: the SVG elements in ``SVG_ELEMENTS`` (plus a ``style`` element whose CSS has no imports, escapes or external urls) and
    their drawing attributes. Dropped, silently: scripts, event handlers, embedded or linked images and documents, foreign
    namespaces (editor metadata), comments, processing instructions and any link that is not ``#fragment``. Refused: a file that
    is not an SVG, is malformed or too large, or declares entities (a DOCTYPE with an internal subset)."""
    maximum = 2 * 1024**2
    content = source.read(maximum + 1)
    if len(content) > maximum or b"\0" in content:
        raise BackupError("The SVG image is too large or not valid.")
    if re.search(rb"<!DOCTYPE[^>]*\[", content, re.I) or b"<!ENTITY" in content:
        raise BackupError("The SVG image declares entities, which are not allowed.")
    content = re.sub(rb"<!DOCTYPE[^>\[]*>", b"", content, flags=re.I)
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise BackupError("The SVG image is malformed.") from error
    if root.tag != f"{SVG_NAMESPACE}svg":
        raise BackupError("The SVG image is malformed.")

    def clean(element):
        for key in list(element.attrib):
            value = element.attrib[key]
            if key in SVG_HREF:
                if not re.fullmatch(SVG_FRAGMENT, value):
                    del element.attrib[key]
                continue
            if key not in SVG_ATTRIBUTES:
                del element.attrib[key]
                continue
            fragments = re.sub(rf"url\(\s*['\"]?{SVG_FRAGMENT}['\"]?\s*\)", "", value)
            if key == "style":
                if _SVG_UNSAFE_CSS.search(value) or not re.fullmatch(r"[a-zA-Z0-9_.#,%;:\s+\-()'\"/]*", value):
                    del element.attrib[key]
            elif key in {"transform", "gradientTransform", "patternTransform"}:
                if not re.fullmatch(r"[a-zA-Z0-9.,()\s+\-]*", value):
                    del element.attrib[key]
            elif key in {"fill", "stroke", "stop-color", "color"} and re.fullmatch(r"(?:rgb|rgba|hsl|hsla)\([\d.,%\s]+\)", value):
                continue
            elif any(part in fragments.lower() for part in ("\\", "url", ":", "@", "(", ")")):
                del element.attrib[key]
        for child in list(element):
            name = child.tag.removeprefix(SVG_NAMESPACE) if isinstance(child.tag, str) and child.tag.startswith(SVG_NAMESPACE) else None
            if name == "style":
                text = "".join(child.itertext())
                if _SVG_UNSAFE_CSS.search(text) or len(text) > 200_000:
                    element.remove(child)
                else:
                    child.attrib.clear()
                    for grandchild in list(child):
                        child.remove(grandchild)
                continue
            if name not in SVG_ELEMENTS:
                element.remove(child)
                continue
            clean(child)

    clean(root)
    for prefix, uri in _SVG_NAMESPACES.items():
        ElementTree.register_namespace(prefix, uri)
    return ElementTree.tostring(root, encoding="utf-8")


def _safe_path(name):
    path = PurePosixPath(name)
    return "\\" not in name and not path.is_absolute() and ".." not in path.parts and str(path) == name


@contextmanager
def _open_backup(upload):
    """Open a bounded archive and normalize v1 records to the current schema, without checking the records yet."""
    from .legacy_backup import convert_v1

    if upload.size > MAX_UPLOAD:
        raise BackupError("The ZIP exceeds the 512 MiB upload limit.")
    try:
        with ZipFile(upload) as archive:
            entries = archive.infolist()
            names = {item.filename for item in entries}
            if len(entries) > 100_000 or len(names) != len(entries) or any(not _safe_path(name) for name in names):
                raise BackupError("The ZIP contains invalid or duplicate paths.")
            if any(name != "manifest.json" and not name.startswith("media/") for name in names):
                raise BackupError("The ZIP contains entries outside manifest.json and media/.")
            if any(item.flag_bits & 1 for item in entries):
                raise BackupError("Encrypted ZIP entries are unsupported.")
            if sum(item.file_size for item in entries) > MAX_EXPANDED or archive.getinfo("manifest.json").file_size > MAX_MANIFEST:
                raise BackupError("The expanded backup exceeds the size limit.")
            manifest = json.loads(archive.read("manifest.json"))
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("version") not in (1, 2):
                raise BackupError("Choose a RosterChief v1 or v2 club backup.")
            version = manifest["version"]
            warnings = []
            if version == 1:
                manifest, warnings = convert_v1(manifest)
            yield manifest, archive, version, warnings, names
    except BackupError:
        raise
    except (BadZipFile, CompressionError, KeyError, TypeError, AttributeError, OverflowError, NotImplementedError, RecursionError, ValueError, ValidationError) as error:
        raise BackupError("The backup contains invalid or unsupported data.") from error


@contextmanager
def read_backup(upload):
    """``_open_backup`` that refuses a backup with anything that cannot be restored; the error carries the full report."""
    with _open_backup(upload) as (manifest, archive, version, warnings, names):
        report = analyse(manifest, names)
        if report["blocked"]:
            first = next((reason for section in report["sections"] for item in section["items"] for reason in item["reasons"]), None) or report["club"][0]
            raise BackupError(first, report=report)
        yield manifest, archive, version, warnings


MAX_LISTED = 500  # records listed per section in a report; the blocked ones always come first


def _describe(fields, pk):
    """A readable name for a record in the report."""
    for key in ("name", "title", "subject", "label", "description", "code", "email"):
        if isinstance(fields.get(key), str) and fields[key].strip():
            return fields[key].strip()[:80]
    person = f"{fields.get('first_name') or ''} {fields.get('last_name') or ''}".strip()
    return person[:80] or str(pk)[:8]


def analyse(manifest, names, exclude=()):
    """Everything the backup would restore, record by record, and what cannot be restored and why.

    Returns ``{"total", "blocked", "restorable", "club": [problems that stop the whole restore], "sections": [{"key", "name",
    "total", "blocked", "items": [{"id", "label", "reasons"}]}], "skip": {section key: [ids of every blocked record]}}``. A record is blocked by its own problem (a missing file, an unknown
    reference, an invalid value) or because it depends on a blocked record -- restoring it would leave a dangling reference.
    ``exclude``: section keys the person chose to leave out; their records count as blocked ("Left out by your choice") and so
    does whatever depends on them. Accounts and roles cannot be left out. The report also has ``refs`` (section key -> the
    section keys its records point at) and each section its ``group``.
    """
    club_problems = []

    def guard(check, *args):
        try:
            check(*args)
        except BackupError as error:
            club_problems.append(str(error))
        except (KeyError, TypeError, AttributeError, ValueError, ValidationError):
            club_problems.append("The backup contains invalid or unsupported data.")

    club, accounts, roles, records = (manifest[key] for key in ("club", "accounts", "roles", "records"))
    model_map = tenant_models()
    entries = {}  # (label, id) -> {"label", "reasons"}
    order = []  # sections in display order
    ids = {}
    depends = defaultdict(set)  # (label, id) -> the (label, id) pairs it points at

    def section(key, name):
        if key not in order:
            order.append(key)

    def entry(key, pk, label, reasons=None):
        entries[(key, pk)] = {"label": label, "reasons": list(reasons or [])}

    # Accounts and roles: a problem with one blocks it and, below, everything that points at it.
    role_slugs = {role["slug"] for role in roles}
    seen_emails, seen_accounts = set(), set()
    user_key = User._meta.label_lower
    account_ids = set()
    for account in accounts:
        pk = str(account["id"])
        account_ids.add(pk)
        reasons = []
        try:
            validate_email(account["email"])
        except ValidationError:
            reasons.append("The email address is invalid.")
        email = account["email"].strip().casefold()
        if pk in seen_accounts or email in seen_emails:
            reasons.append("The backup contains duplicate login identities.")
        seen_accounts.add(pk)
        seen_emails.add(email)
        if set(account.get("roles", ())) - role_slugs:
            reasons.append("An account references a missing club role.")
        if set(account.get("permissions", ())) - PERMISSION_CATALOG.keys():
            reasons.append("An account references an unsupported club permission.")
        entry("accounts", pk, account["email"], reasons)
        section("accounts", "Accounts")
    for role in roles:
        reasons = ["The role has an unsupported permission."] if set(role["permissions"]) - PERMISSION_CATALOG.keys() else []
        if sum(other["slug"] == role["slug"] for other in roles) > 1:
            reasons.append("The backup contains duplicate role slugs.")
        entry("roles", role["slug"], role["name"], reasons)
        section("roles", "Roles")
    owner = next((account for account in accounts if str(account["id"]) == str(club["owner"])), None)
    if not owner or not owner.get("enrolled") or not owner.get("active", True):
        club_problems.append("The backup must specify an active, enrolled club owner.")
    if not club["fields"].get("name", "").strip():
        club_problems.append("The backup has no club name.")
    if not isinstance(club.get("slug"), str) or not club["slug"].strip():
        club_problems.append("The backup has no club slug.")
    if set(manifest.get("features", {})) - FEATURE_CATALOG.keys():
        club_problems.append("The backup contains an unsupported feature.")

    for label, rows in records.items():
        model = model_map.get(label)
        name = str(model._meta.verbose_name_plural).capitalize() if model else label
        section(label, name)
        if model is None or not isinstance(rows, list):
            for row in rows if isinstance(rows, list) else ():
                entry(label, str(row.get("fields", {}).get("id", len(entries))), _describe(row.get("fields", {}), "?"), [f"Unsupported backup model: {label}."])
            if not isinstance(rows, list):
                club_problems.append(f"Unsupported backup model: {label}.")
            continue
        pk_field = model._meta.pk
        ids[label] = set()
        counts = defaultdict(int)
        for row in rows:
            counts[str(pk_field.to_python(row["fields"].get(pk_field.attname)))] += 1
        ids[label] = set(counts)

    ids[user_key] = account_ids
    ids[RefereeType._meta.label_lower] = set(manifest.get("referee_types", {}))
    club_fields = {key: field for key, field in _fields(Club).items() if key in CLUB_FIELDS}
    guard(_validate_fields, "club.club", {key: value for key, value in club["fields"].items() if key != "name"}, club_fields, names, ids)

    for label, rows in records.items():
        model = model_map.get(label)
        if model is None or not isinstance(rows, list):
            continue
        fields, many = _fields(model), _many(model)
        pk_field = model._meta.pk
        counts = defaultdict(int)
        for row in rows:
            counts[str(pk_field.to_python(row["fields"].get(pk_field.attname)))] += 1
        for row in rows:
            values = row["fields"]
            pk = str(pk_field.to_python(values.get(pk_field.attname)))
            reasons = []
            if pk == "None" or counts[pk] > 1:
                reasons.append(f"Missing or duplicate ID for {label}.")
            try:
                _validate_fields(label, values, fields, names, ids)
            except (BackupError, ValueError, TypeError, ValidationError) as error:
                reasons.append(str(error) if isinstance(error, BackupError) else f"Invalid record for {label}.")
            if label == "registration.registrationbatch" and (history := values.get("legacy_invoice_history")):
                try:
                    _validate_legacy_history(history)
                except BackupError as error:
                    reasons.append(str(error))
            for key, related in row.get("many", {}).items():
                if key not in many or not isinstance(related, list):
                    reasons.append(f"Invalid relationship {label}.{key}.")
                    continue
                target = many[key].related_model._meta.label_lower
                if len(set(map(str, related))) != len(related) or set(map(str, related)) - ids.get(target, set()):
                    reasons.append(f"Invalid relationship {label}.{key}.")
                depends[(label, pk)].update((target, str(other)) for other in related)
            for name, value in values.items():
                field = fields.get(name)
                if field is not None and field.is_relation and not isinstance(field, models.FileField) and value is not None:
                    depends[(label, pk)].add((field.related_model._meta.label_lower, str(value)))
            if (label, pk) in entries and pk != "None":  # a duplicate: list it once, with the problem
                entries[(label, pk)]["reasons"].extend(reason for reason in reasons if reason not in entries[(label, pk)]["reasons"])
            else:
                entry(label, pk, _describe(values, pk), reasons)

    for (label, pk), item in entries.items():
        if label in exclude and label not in ("accounts", "roles"):
            item["reasons"].append("Left out by your choice.")
    # Cascade: whatever points at a blocked record is blocked too.
    redirect = {user_key: "accounts"}
    blocked = {key for key, item in entries.items() if item["reasons"]}
    changed = True
    while changed:
        changed = False
        for key, targets in depends.items():
            if key in blocked or key not in entries:
                continue
            hit = next((target for target in targets if (redirect.get(target[0], target[0]), target[1]) in blocked), None)
            if hit:
                hit_key = (redirect.get(hit[0], hit[0]), hit[1])
                target_model = tenant_models().get(hit[0])
                kind = str(target_model._meta.verbose_name) if target_model else "account"
                entries[key]["reasons"].append(f"Depends on {kind} “{entries[hit_key]['label']}”, which cannot be restored.")
                blocked.add(key)
                changed = True

    if owner and entries.get(("accounts", str(owner["id"])), {}).get("reasons"):
        club_problems.append("The club owner's account cannot be restored, so the club cannot be restored either.")
    skip = defaultdict(list)
    for (label, pk), item in entries.items():
        if item["reasons"]:
            skip[label].append(pk)

    sections = []
    for key in order:
        items = [{"id": pk, **item} for (label, pk), item in entries.items() if label == key]
        items.sort(key=lambda item: not item["reasons"])
        name = "Accounts" if key == "accounts" else "Roles" if key == "roles" else (str(model_map[key]._meta.verbose_name_plural).capitalize() if key in model_map else key)
        app = key.split(".")[0]
        group = key if key in ("accounts", "roles") else app
        sections.append({"key": key, "name": name, "group": group, "total": len(items), "blocked": sum(bool(item["reasons"]) for item in items), "items": items[:MAX_LISTED], "truncated": max(len(items) - MAX_LISTED, 0)})
    total = sum(section["total"] for section in sections)
    blocked_total = sum(section["blocked"] for section in sections)
    refs = defaultdict(set)
    for (label, _pk), targets in depends.items():
        refs[label].update(redirect.get(target[0], target[0]) for target in targets if redirect.get(target[0], target[0]) != label)
    return {"total": total, "blocked": blocked_total + len(club_problems), "restorable": total - blocked_total, "club": club_problems, "sections": sections, "skip": dict(skip), "refs": {label: sorted(targets) for label, targets in refs.items()}}


def _validate_legacy_history(history):
    try:
        amounts = [history[key] for key in ("subtotal", "discount_amount", "manual_discount_amount", "total")]
        if not isinstance(history["lines"], list) or not history["lines"]:
            raise ValueError
        for line in history["lines"]:
            if any(not isinstance(line[key], str) for key in ("id", "membership_id", "member", "description")):
                raise ValueError
            amounts.extend(line[key] for key in ("price", "discount_amount"))
        if any(not (parsed := Decimal(str(amount))).is_finite() or abs(parsed) > Decimal("99999999.99") for amount in amounts):
            raise ValueError
    except (TypeError, KeyError, ValueError, InvalidOperation) as error:
        raise BackupError("A legacy invoice snapshot has invalid lines or amounts.") from error


def _validate_fields(label, values, fields, names, ids):
    if not isinstance(values, dict):
        raise BackupError(f"Invalid record for {label}.")
    if extra := values.keys() - fields.keys():
        raise BackupError(f"Unsupported fields for {label}: {', '.join(sorted(extra))}.")
    for name, value in values.items():
        field = fields[name]
        if isinstance(field, models.FileField):
            if value and (not isinstance(value, str) or not value.startswith("media/") or value not in names):
                raise BackupError(f"A file referenced by {label}.{name} is missing.")
        elif field.is_relation and value is not None:
            if str(value) not in ids.get(field.related_model._meta.label_lower, ()):
                raise BackupError(f"Missing referenced record for {label}.{name}.")
        elif not field.is_relation and value is not None:
            parsed = field.to_python(value)
            if field.max_length and isinstance(parsed, str) and len(parsed) > field.max_length:
                raise BackupError(f"A value for {label}.{name} is too long.")
        elif value is None and not field.null:
            raise BackupError(f"A value for {label}.{name} is required.")


def preview(upload):
    """What the backup holds and whether it can be restored: ``report`` lists every record and the ones that cannot be."""
    with _open_backup(upload) as (manifest, archive, version, warnings, names):
        report = analyse(manifest, names)
        return {
            "version": version, "name": manifest["club"]["fields"]["name"], "slug": manifest["club"]["slug"],
            "accounts": sum(bool(account.get("enrolled")) for account in manifest["accounts"]),
            "records": sum(len(rows) for rows in manifest["records"].values()),
            "files": sum(name.startswith("media/") for name in archive.namelist()), "warnings": warnings, "report": report,
        }


class _Restore:
    def __init__(self, archive):
        self.archive = archive
        self.written = []
        self.ids = defaultdict(dict)
        self.expanded_files = 0

    def save_file(self, reference, field, club):
        if not reference:
            return ""
        info = self.archive.getinfo(reference)
        self.expanded_files += info.file_size
        if self.expanded_files > MAX_EXPANDED:
            raise BackupError("The files referenced by the backup exceed the expanded size limit.")
        filename = get_valid_filename(PurePosixPath(reference).name)
        # Public image extensions follow the decoded content. Documents keep their safe extension.
        from rosterchief.storage import get_private_storage

        svg = None
        if field.model._meta.label_lower == "shop.productattachment":
            from events.services.attachments import ALLOWED_EXTENSIONS

            if PurePosixPath(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
                raise BackupError(f"Unsupported public attachment: {filename}")
        elif field.storage is not get_private_storage():
            if PurePosixPath(filename).suffix.lower() == ".svg":
                with self.archive.open(info) as source:
                    svg = _static_svg(source)
                extension = ".svg"
            else:
                try:
                    with self.archive.open(info) as source, Image.open(source) as image:
                        extension = IMAGE_EXTENSIONS[image.format]
                        image.verify()
                except (UnidentifiedImageError, Image.DecompressionBombError, OSError, KeyError) as error:
                    raise BackupError(f"Unsupported or corrupt public image: {filename}") from error
            filename = f"{PurePosixPath(filename).stem[:80]}{extension}"
        else:
            filename = filename[-85:]
        target = f"restore/{club.pk.hex}/{uuid4().hex[:16]}/{filename}"
        content = ContentFile(svg, name=filename) if svg is not None else self.archive.open(info)
        with content:
            stored = field.storage.save(target, File(content, name=filename), max_length=field.max_length)
        self.written.append((field.storage, stored))
        return stored

    def fields(self, values, fields, club):
        result = {}
        for name, value in values.items():
            field = fields[name]
            if isinstance(field, models.FileField):
                result[name] = self.save_file(value, field, club)
            elif field.is_relation and value is not None:
                mapped = self.ids[field.related_model]
                result[name] = mapped[str(value)] if str(value) in mapped else field.target_field.to_python(value)
            else:
                result[name] = field.to_python(value)
        return result

    def records(self, records, club):
        model_map = tenant_models()
        # PostgreSQL's Django foreign keys are deferred, so self-links and cycles can be restored with their original IDs.
        for label, rows in records.items():
            model = model_map[label]
            fields = _fields(model)
            for row in rows:
                obj = model(**self.fields(row["fields"], fields, club))
                obj.save_base(raw=True, force_insert=True)
        # Restore relation rows directly. M2M signals would re-evaluate each event's
        # partially restored audience and could discard its existing RSVP records.
        for label, rows in records.items():
            model = model_map[label]
            pk = model._meta.pk.attname
            for name, field in _many(model).items():
                through = field.remote_field.through
                source = f"{field.m2m_field_name()}_id"
                target = f"{field.m2m_reverse_field_name()}_id"
                links = [
                    through(**{source: row["fields"][pk], target: related_id})
                    for row in rows
                    for related_id in row.get("many", {}).get(name, ())
                ]
                through._base_manager.bulk_create(links, batch_size=500)
        with connection.cursor() as cursor:
            for statement in connection.ops.sequence_reset_sql(no_style(), [model_map[label] for label in records]):
                cursor.execute(statement)


def _restore_sport(source):
    if not source:
        return None
    sport = Sport.objects.filter(slug=source["slug"]).first()
    if sport is None:
        sport = Sport.objects.create(slug=source["slug"], name=source["name"])
    return sport


def _restore_accounts(accounts, restore):
    new_accounts = 0
    for account in accounts:
        email = account["email"].strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            user = clubs.create_account(email, account.get("first_name", ""), account.get("last_name", ""))
            if not account.get("active", True):
                User.objects.filter(pk=user.pk).update(is_active=False)
                user.is_active = False
            new_accounts += bool(account.get("enrolled") and user.is_active)
        elif account.get("enrolled"):
            # Reuse a verified identity without changing its credentials or global status.
            user, _ = clubs.resolve_account(email)
        restore.ids[User][str(account["id"])] = user.pk
    return new_accounts


def _restore_roles(club, manifest, restore):
    current = {role.slug: role for role in Role.objects.select_related("group")}
    permissions = sync_permissions()
    for data in sorted(manifest["roles"], key=lambda role: role["slug"] not in current):
        slug = data["slug"]
        if slug == "owner":
            continue
        role = current.get(slug)
        if role:
            role.description = data.get("description", "")
            role.save(update_fields=["description"])
            if role.group.name != data["name"]:
                role.group.name = data["name"]
                role.group.save(update_fields=["name"])
            set_role_perms(role, data["permissions"])
        else:
            current[slug] = create_role(data["name"], data["permissions"], slug=slug, description=data.get("description", ""))
    for account in manifest["accounts"]:
        if not account.get("enrolled"):
            continue
        user = User.objects.get(pk=restore.ids[User][str(account["id"])])
        if user.pk != club.owner_id:
            club.add_user(user, is_staff=bool(account.get("staff")), is_superuser=bool(account.get("superuser")))
        for slug in account.get("roles", ()):
            assign_role(user, current[slug])
        user.tenant_perms.user_permissions.set([permissions[code] for code in account.get("permissions", ())])


def _restore_features(club, values):
    target = resolve_entitlements(club)
    features = Feature.objects.in_bulk(values.keys(), field_name="code")
    overrides = []
    for code, value in values.items():
        if code not in features:
            continue
        enabled, limit = value["enabled"], value.get("limit")
        if (enabled, limit) != (target.has(code), target.limit(code)):
            overrides.append(FeatureOverride(club=club, feature=features[code], enabled=enabled, limit=limit, reason="Restored from a club backup"))
    FeatureOverride.objects.bulk_create(overrides)


def _valid_slug(slug):
    return (
        len(slug) <= clubs.SLUG_MAX_LENGTH
        and slug not in clubs.RESERVED_SLUGS
        and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) is not None
    )


def suggest_restore_slug(source_slug):
    """Keep an available source address or suggest a fresh address for the restored copy."""
    with schema_context(get_public_schema_name()):
        if _valid_slug(source_slug) and not clubs.club_slug_taken(source_slug):
            return source_slug
        base = re.sub(r"-+", "-", slugify(source_slug).replace("_", "-")).strip("-") or "club"
        index = 1
        while True:
            suffix = "-restored" if index == 1 else f"-restored-{index}"
            suggestion = base[: clubs.SLUG_MAX_LENGTH - len(suffix)].rstrip("-") + suffix
            if not clubs.club_slug_taken(suggestion):
                return suggestion
            index += 1


def _without_blocked(manifest, report):
    """``manifest`` minus the records, accounts and roles the report blocked. Whatever depended on one of them is blocked too (see
    ``analyse``), so what is left refers only to what is left."""
    skip = {key: set(ids) for key, ids in report["skip"].items()}
    kept = {**manifest, "records": {label: [row for row in rows if _record_id(label, row) not in skip.get(label, ())] for label, rows in manifest["records"].items()}}
    kept["accounts"] = [account for account in manifest["accounts"] if str(account["id"]) not in skip.get("accounts", ())]
    kept["roles"] = [role for role in manifest["roles"] if role["slug"] not in skip.get("roles", ())]
    removed = skip.get("roles", set())
    kept["accounts"] = [{**account, "roles": [slug for slug in account.get("roles", ()) if slug not in removed]} for account in kept["accounts"]]
    return kept


def _record_id(label, row):
    pk_field = tenant_models()[label]._meta.pk
    return str(pk_field.to_python(row["fields"][pk_field.attname]))


def restore_club(upload, *, name="", slug="", on_restore=None, partial=False, exclude=()):
    """Restore to a new tenant. The tenant and all database changes commit together.

    A backup with anything that cannot be restored is refused (the error carries the report) unless ``partial``: then the blocked
    records, and the ones depending on them, are left out and listed in the returned warnings. Problems with the club itself
    (no owner, ...) always refuse. ``exclude``: section keys to leave out on purpose (with whatever depends on them), which does
    not need ``partial``; a backup with real errors still does."""
    with _open_backup(upload) as (manifest, archive, version, warnings, names):
        report = analyse(manifest, names)
        if report["club"] or (report["blocked"] and not partial):
            first = report["club"][0] if report["club"] else next(reason for section in report["sections"] for item in section["items"] for reason in item["reasons"])
            raise BackupError(first, report=report)
        exclude = {key for key in exclude if key}
        if exclude:
            report = analyse(manifest, names, exclude)
        if report["blocked"]:
            manifest = _without_blocked(manifest, report)
            skipped = [f"{section['blocked']} of {section['total']} {section['name'].lower()}" for section in report["sections"] if section["blocked"]]
            warnings = [*warnings, "Not restored: " + ", ".join(skipped) + "."]
        restore = _Restore(archive)
        try:
            with transaction.atomic(), schema_context(get_public_schema_name()):
                source = manifest["club"]
                requested_name = name.strip() or source["fields"]["name"]
                requested_slug = slug.strip() or source["slug"]
                if len(requested_name) > Club._meta.get_field("name").max_length:
                    raise BackupError("The new club name must have at most 100 characters.", field="name")
                if not _valid_slug(requested_slug):
                    raise BackupError("Choose a club slug using up to 40 lowercase letters, numbers and hyphens.", field="slug")
                if clubs.club_slug_taken(requested_slug):
                    raise BackupError("That club slug already exists. Choose a different slug to restore a separate club.", field="slug")
                new_accounts = _restore_accounts(manifest["accounts"], restore)
                owner = User.objects.get(pk=restore.ids[User][str(source["owner"])])
                if not owner.is_active:
                    raise BackupError("The backup owner is inactive on this platform.")
                club = Club(name=requested_name, owner=owner, sport=_restore_sport(source.get("sport")))
                clubs.provision_club(club, preferred_slug=requested_slug)
                fields = {key: field for key, field in _fields(Club).items() if key in CLUB_FIELDS and key != "name"}
                values = {key: value for key, value in source["fields"].items() if key != "name"}
                Club.objects.filter(pk=club.pk).update(**restore.fields(values, fields, club))
                club.refresh_from_db()
                for key, item in manifest.get("referee_types", {}).items():
                    referee_type, _ = RefereeType.objects.get_or_create(
                        sport=_restore_sport(item["sport"]), kind=item["kind"], name=item["name"], defaults={"ordering": item["ordering"]}
                    )
                    restore.ids[RefereeType][key] = referee_type.pk
                _restore_features(club, manifest.get("features", {}))
                with schema_context(club.schema_name):
                    from shop.models import ProductCategory

                    # Provisioning seeds an Owner roster entry and the two built-in shop categories.
                    if any(row["fields"].get("user_id") == str(source["owner"]) for row in manifest["records"].get("members.member", ())):
                        apps.get_model("members", "Member").objects.filter(user_id=owner.pk).delete()
                    imported_keys = {row["fields"].get("system_key") for row in manifest["records"].get("shop.productcategory", ())}
                    ProductCategory.objects.filter(system_key__in=imported_keys - {None}).delete()
                    # Tenant migrations can switch initially deferred constraints to immediate
                    # in this transaction. Re-enable deferral for the full record graph.
                    with connection.cursor() as cursor:
                        cursor.execute("SET CONSTRAINTS ALL DEFERRED")
                    restore.records(manifest["records"], club)
                    _restore_roles(club, manifest, restore)
                    # Check references in the tenant while this schema is selected.
                    with connection.cursor() as cursor:
                        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                        cursor.execute("SET CONSTRAINTS ALL DEFERRED")
                if on_restore is not None:
                    on_restore(club, version)
        except BaseException as error:
            for storage, path in reversed(restore.written):
                try:
                    storage.delete(path)
                except OSError:
                    logger.exception("Could not remove file from a failed backup restore: %s", path)
            if isinstance(error, DatabaseError):
                cause = error.__cause__
                diagnostic = getattr(cause, "diag", None)
                logger.warning(
                    "Club backup restore failed (%s, table=%s, column=%s, constraint=%s).",
                    getattr(cause, "sqlstate", type(error).__name__),
                    getattr(diagnostic, "table_name", None),
                    getattr(diagnostic, "column_name", None),
                    getattr(diagnostic, "constraint_name", None),
                )
            if isinstance(error, (DatabaseError, clubs.AccountError)):
                raise BackupError("The backup could not be restored. Check for account conflicts or records that violate the current schema.") from error
            raise
        if club.logo and club.logo.name.lower().endswith(".svg"):
            warnings = [*warnings, "The club logo is an SVG. It is shown as the club crest, but browser and home-screen icons (favicon, app icon) cannot be made from an SVG, so browsers use the default icon. Upload a PNG, JPEG or WebP logo in the club settings to get them."]
        return club, new_accounts, warnings
