"""Build a portable v1 backup from one legacy club's database snapshot."""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import PurePosixPath
from tempfile import SpooledTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import validate_email
from django.db import connection, models, transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from django_countries.fields import Country
from phonenumber_field.phonenumber import PhoneNumber

from club.mixins import OFFICIALS_FLAG
from club.models import Club, ClubMembership, ClubRole, RefereeType, Sport
from features.models import Flag
from members.models import FamilyMembership, Member
from registration.services.pricing import available_registration_products
from shop.models import ProductVariant

FORMAT = "rosterchief-club-backup"
VERSION = 1
MAX_ZIP = 512 * 1024**2
MAX_EXPANDED = 2 * 1024**3
MAX_MANIFEST = 32 * 1024**2
MAX_ENTRIES = 100_000
CHUNK_SIZE = 1024**2
SIZE_ERROR = "This club exceeds the size limits of a portable backup."

# These models own their club FK. Everything else is scoped through its owning
# parent below, with all remaining references checked against the resulting graph.
CLUB_MODELS = {
    "club": "season sponsor clubmembership duesinvoice onboardingrequirement clubrole shopmanager refereemanager evaluationmanager",
    "members": "family group",
    "teams": "numberpool team position numberreservation refereelevel refereeprofile officiallevel officialprofile",
    "events": "opponent location event eventseries gamefollow eventfollow",
    "news": "news",
    "formbuilder": "form formsend",
    "shop": "productcategory product order discount voucher invoice",
    "registration": "registrationbatch",
    "evaluations": "evaluationchecklist playerevaluation evaluationnote evaluationsession evaluationoutcome",
}
CHILD_SCOPES = {
    "club.feepayment": "membership__club_id",
    "club.memberrequirementstatus": "membership__club_id",
    "members.familymembership": "family__club_id",
    "members.groupmembership": "group__club_id",
    "teams.teamphoto": "team__club_id",
    "teams.teammembership": "team__club_id",
    "teams.staffassignment": "team__club_id",
    "events.attendance": "event__club_id",
    "events.lineup": "event__club_id",
    "events.lineupselection": "lineup__event__club_id",
    "events.eventreferee": "event__club_id",
    "events.eventofficial": "event__club_id",
    "events.refereesignup": "event__club_id",
    "events.officialsignup": "event__club_id",
    "events.eventtask": "event__club_id",
    "events.eventtaskclaim": "task__event__club_id",
    "events.eventattachment": "event__club_id",
    "events.gametimeline": "event__club_id",
    "news.newsphoto": "news_item__club_id",
    "formbuilder.field": "form__club_id",
    "formbuilder.submission": "send__club_id",
    "formbuilder.answer": "submission__send__club_id",
    "shop.productvariant": "product__club_id",
    "shop.productregistrantdiscounttier": "product__club_id",
    "shop.orderline": "order__club_id",
    "shop.inventorymovement": "product__club_id",
    "shop.applieddiscount": "order__club_id",
    "shop.voucherconsumption": "voucher__club_id",
    "shop.payment": "order__club_id",
    "registration.registrationdetails": "batch__club_id",
}
ENROLLING_ROWS = ("club.clubrole", "club.shopmanager", "club.refereemanager", "club.evaluationmanager", "teams.staffassignment")
FEATURE_FLAGS = {
    "shop": "shop",
    "referee_officials": OFFICIALS_FLAG,
    "evaluations": "evaluations",
    "competition_rbihf": "RBIHF",
    "competition_dfel": "DFEL",
    "competition_ijnl": "IJNL",
}
User = get_user_model()


class BackupError(ValueError):
    """A source club cannot be exported in the portable format."""


class BackupJSONEncoder(DjangoJSONEncoder):
    def default(self, value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, (Country, PhoneNumber)):
            return str(value)
        return super().default(value)


def _club_login_members(club_id):
    # Match mobile.access.has_club_tie's relationship rules, excluding the
    # platform override. An inactive login still has a recorded club tie.
    live = ClubMembership.objects.filter(club_id=club_id).exclude(status=ClubMembership.StatusChoices.CANCELLED)
    families_with_live_children = FamilyMembership.objects.filter(
        family__club_id=club_id, role=FamilyMembership.FamilyRole.CHILD, member_id__in=live.values("member_id")
    )
    guardians = FamilyMembership.objects.filter(
        family_id__in=families_with_live_children.values("family_id"),
        role__in=(FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN),
    )
    tied = Q(pk__in=live.values("member_id")) | Q(pk__in=guardians.values("member_id"))
    for label in ENROLLING_ROWS:
        scope = CHILD_SCOPES.get(label, "club_id")
        ties = apps.get_model(label).objects.filter(**{scope: club_id})
        tied |= Q(pk__in=ties.values("member_id"))
    return Member.objects.filter(tied, user_id__isnull=False)


def owner_candidates(club_id):
    """Active club admins take precedence. Otherwise offer active club logins."""
    admins = ClubRole.objects.filter(club_id=club_id, role=ClubRole.Roles.ADMIN).values("member_id")
    active = _club_login_members(club_id).filter(user__is_active=True).select_related("user").order_by("user__email")
    admins = list(active.filter(pk__in=admins))
    return (admins, True) if admins else (list(active), False)


def _sport(sport):
    return {"slug": sport.code.replace("_", "-"), "name": sport.name} if sport else None


def _features(club_id):
    flags = Flag.objects.filter(name__in=FEATURE_FLAGS.values())
    targeted = set(flags.filter(clubs=club_id).values_list("name", flat=True))
    everyone = dict(flags.values_list("name", "everyone"))
    values = {
        code: {"enabled": (name in targeted if everyone.get(name) is None else everyone[name]), "limit": None}
        for code, name in FEATURE_FLAGS.items()
    }
    # Sponsor management is available to all legacy clubs.
    values["sponsoring"] = {"enabled": True, "limit": None}
    return values


def _registration_open(club):
    products = available_registration_products(club).filter(season__club=club)
    return not club.is_archived and ProductVariant.objects.filter(product__in=products, is_active=True).exists()


class ClubSnapshot:
    def __init__(self):
        self.records = {}
        self.ids = defaultdict(set)
        self.references = defaultdict(set)
        self.files = []

    def reference(self, field, value, source):
        target = field.related_model._meta.label_lower
        self.references[target].add((value, source))
        return str(value)

    def fields(self, obj):
        values = {}
        for field in obj._meta.concrete_fields:
            name = field.attname
            if name == "club_id":
                continue
            value = getattr(obj, name)
            if isinstance(field, models.FileField):
                value = self.file_reference(obj, field, value)
            elif value is not None:
                if field.is_relation:
                    value = self.reference(field, value, f"{obj._meta.label_lower}.{name}")
                elif field.primary_key:
                    value = str(value)
            values[name] = value
        return values

    def file_reference(self, obj, field, file):
        if not file:
            return ""
        basename = PurePosixPath(file.name.replace("\\", "/")).name
        if obj._meta.label_lower == "events.eventattachment":
            basename = PurePosixPath(obj.name.replace("\\", "/")).name or basename
        elif obj._meta.label_lower == "formbuilder.answer":
            basename = PurePosixPath(obj.file_name.replace("\\", "/")).name or basename
        if basename in {"", ".", ".."} or "\x00" in basename:
            raise BackupError(f"Invalid file name on {obj._meta.label_lower}.{field.name} (record {obj.pk}).")
        path = f"media/{len(self.files) + 1}/{basename}"
        self.files.append((path, file, f"{obj._meta.label_lower}.{field.name} (record {obj.pk})"))
        return path

    def collect(self, queryset):
        model = queryset.model
        label = model._meta.label_lower
        rows = {}
        for obj in queryset.order_by("pk").iterator():
            rows[obj.pk] = {"fields": self.fields(obj), "many": {}}
        self.ids[label] = set(rows)
        self.records[label] = list(rows.values())
        if not rows:
            return
        for field in model._meta.many_to_many:
            through = field.remote_field.through
            if not through._meta.auto_created:
                continue
            source_id = f"{field.m2m_field_name()}_id"
            target_id = f"{field.m2m_reverse_field_name()}_id"
            links = through._base_manager.filter(**{f"{source_id}__in": queryset.order_by().values("pk")})
            for source, target in links.order_by(source_id, target_id).values_list(source_id, target_id).iterator():
                rows[source]["many"].setdefault(field.name, []).append(self.reference(field, target, f"{label}.{field.name}"))

    def referenced_ids(self, model):
        return {pk for pk, _ in self.references[model._meta.label_lower]}

    def validate_references(self):
        for target, references in self.references.items():
            for pk, source in references:
                if pk not in self.ids[target]:
                    raise BackupError(f"{source} references a missing or out-of-club {target} record ({pk}).")

    def manifest(self, club, owner_id):
        candidates, has_admin = owner_candidates(club.pk)
        choices = {member.user_id for member in candidates}
        if not choices:
            raise BackupError("This club needs an active club login before it can be backed up.")
        if owner_id is None and has_admin and len(choices) == 1:
            owner_id = next(iter(choices))
        if owner_id not in choices:
            raise BackupError("Select an eligible club login as the restore owner.")

        for app, names in CLUB_MODELS.items():
            for name in names.split():
                self.collect(apps.get_model(app, name).objects.filter(club_id=club.pk))
        for label, scope in CHILD_SCOPES.items():
            self.collect(apps.get_model(label).objects.filter(**{scope: club.pk}))

        tied = _club_login_members(club.pk)
        enrolled = set(tied.values_list("user_id", flat=True))
        member_ids = self.referenced_ids(Member)
        self.collect(Member.objects.filter(Q(pk__in=member_ids) | Q(pk__in=tied.values("pk"))))
        accounts = self.accounts(enrolled)
        types = RefereeType.objects.filter(pk__in=self.referenced_ids(RefereeType)).select_related("sport")
        referee_types = {}
        for item in types:
            self.ids[RefereeType._meta.label_lower].add(item.pk)
            referee_types[str(item.pk)] = {"sport": _sport(item.sport), "kind": item.kind, "name": item.name, "ordering": item.ordering}
        club_fields = self.fields(club)
        self.validate_references()

        return {
            "format": FORMAT,
            "version": VERSION,
            "exported_at": timezone.now(),
            "club": {
                "slug": club.slug,
                "owner": str(owner_id),
                "fields": club_fields,
                "sport": _sport(Sport.objects.filter(code=club.sport_type).first()),
                "registration_open": _registration_open(club),
            },
            "accounts": accounts,
            "referee_types": referee_types,
            "features": _features(club.pk),
            "records": self.records,
        }

    def accounts(self, enrolled):
        identities = []
        emails = set()
        ids = enrolled | self.referenced_ids(User)
        # The reverse one-to-one is only used for names of historical actors.
        users = User.objects.filter(pk__in=ids).select_related("member").only("id", "email", "is_active", "member__first_name", "member__last_name")
        for user in users.order_by("pk").iterator():
            email = user.email.strip()
            try:
                validate_email(email)
            except ValidationError as error:
                raise BackupError(f"Login {user.pk} needs a valid email address before it can be exported.") from error
            if email.casefold() in emails:
                raise BackupError(f"Login {user.pk} shares an email address with another login in this backup.")
            emails.add(email.casefold())
            member = getattr(user, "member", None)
            identities.append({
                "id": str(user.pk),
                "email": email,
                "first_name": member.first_name if member else "",
                "last_name": member.last_name if member else "",
                "active": user.is_active,
                "enrolled": user.pk in enrolled,
            })
            self.ids[User._meta.label_lower].add(user.pk)
        return identities

    def write(self, manifest, spool):
        encoded = json.dumps(manifest, cls=BackupJSONEncoder, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        expanded = len(encoded)
        if expanded > MAX_MANIFEST or expanded > MAX_EXPANDED or len(self.files) + 1 > MAX_ENTRIES:
            raise BackupError(SIZE_ERROR)
        with ZipFile(spool, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", encoded)
            for path, file, description in self.files:
                try:
                    source = file.storage.open(file.name, "rb")
                except Exception as error:
                    raise BackupError(f'Could not open "{PurePosixPath(path).name}" on {description}. Check the source upload.') from error
                with source, archive.open(path, "w", force_zip64=True) as target:
                    while True:
                        try:
                            chunk = source.read(CHUNK_SIZE)
                        except Exception as error:
                            raise BackupError(f'Could not read "{PurePosixPath(path).name}" on {description}. Check the source upload.') from error
                        if not chunk:
                            break
                        expanded += len(chunk)
                        if expanded > MAX_EXPANDED:
                            raise BackupError(SIZE_ERROR)
                        target.write(chunk)
                        if spool.tell() > MAX_ZIP:
                            raise BackupError(SIZE_ERROR)
        if spool.tell() > MAX_ZIP:
            raise BackupError(SIZE_ERROR)


def export_club(club_id, *, owner_id=None):
    """Return the club slug and a seekable ZIP for a streaming response."""
    snapshot = ClubSnapshot()
    outer_transaction = connection.in_atomic_block
    with transaction.atomic(durable=True):
        # Django's TestCase is allowed to wrap durable blocks in its rollback
        # transaction, whose isolation level can no longer be changed here.
        if connection.vendor == "postgresql" and not outer_transaction:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            club = Club.objects.get(pk=club_id)
        except Club.DoesNotExist as error:
            raise Http404("Club not found.") from error
        manifest = snapshot.manifest(club, owner_id)

    spool = SpooledTemporaryFile(max_size=8 * 1024**2, mode="w+b")
    try:
        snapshot.write(manifest, spool)
        spool.seek(0)
    except BaseException:
        spool.close()
        raise
    return club.slug, spool
