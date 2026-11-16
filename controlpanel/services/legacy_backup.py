"""Translate the documented v1 archive into the current tenant record format."""

from collections import defaultdict
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from authorization.catalog import CATALOG
from features.catalog import CATALOG as FEATURE_CATALOG

from .club_backup import CLUB_FIELDS, BackupError, _fields, tenant_models

MODEL_MOVES = {
    "club.season": "events.season",
    "club.sponsor": "sponsoring.sponsor",
    "club.clubmembership": "members.clubmembership",
    "club.feepayment": "members.feepayment",
    "club.duesinvoice": "members.duesinvoice",
    "club.onboardingrequirement": "members.onboardingrequirement",
    "club.memberrequirementstatus": "members.memberrequirementstatus",
}
LEGACY_ROLES = {
    "club.clubrole": None,
    "club.shopmanager": "legacy-shop-manager",
    "club.refereemanager": "legacy-referee-manager",
    "club.evaluationmanager": "legacy-evaluation-manager",
}
ROLE_DEFINITIONS = {
    "legacy-club-admin": ("Club admin", list(CATALOG)),
    "legacy-member-admin": ("Member admin", ["members.manage_members"]),
    "legacy-editor": ("Editor", ["news.publish_newsitems", "forms.manage_forms"]),
    "legacy-shop-manager": ("Shop manager", ["shop.manage_shop", *[code for code in CATALOG if code.startswith("shop.") and code != "shop.manage_shop"]]),
    "legacy-referee-manager": ("Referee manager", ["events.manage_referees"]),
    "legacy-evaluation-manager": ("Evaluation manager", ["evaluations.manage_evaluations", "evaluations.execute_evaluations"]),
}
ROLE_CODES = {"admin": "legacy-club-admin", "member_admin": "legacy-member-admin", "editor": "legacy-editor", "member": "member"}


def _id(source, label, pk):
    return str(uuid5(NAMESPACE_URL, f"rosterchief-v1:{source['slug']}:{label}:{pk}"))


def _row(values):
    return {"fields": values, "many": {}}


def _index(records, label):
    return {str(row["fields"]["id"]): row["fields"] for row in records.get(label, ())}


def _registrations(source, records, original, warnings):
    batches = _index(records, "registration.registrationbatch")
    members = _index(records, "members.member")
    memberships = _index(records, "members.clubmembership")
    variants = _index(records, "shop.productvariant")
    products = _index(records, "shop.product")
    for key, legacy in _index(original, "registration.registrationbatch").items():
        batch = batches[key]
        contact = legacy.get("contact_member_id")
        if not contact:
            email = legacy.get("contact_email", "").strip()
            contact = next((pk for pk, member in members.items() if email and member.get("email", "").casefold() == email.casefold()), None)
            if not contact and any(legacy.get(field) for field in ("contact_first_name", "contact_last_name", "contact_email")):
                contact = _id(source, "registration-contact", key)
                member = {
                    "id": contact, "created": legacy["created"], "modified": legacy["modified"],
                    "first_name": legacy.get("contact_first_name", ""), "last_name": legacy.get("contact_last_name", ""),
                    "email": email, "phone": legacy.get("contact_phone") or None,
                }
                records.setdefault("members.member", []).append(_row(member))
                members[contact] = member
        batch.update(
            contact_id=contact, submitted_at=legacy["created"], confirmed_at=legacy.get("invoice_sent_at"),
            confirmation_sent_at=legacy["created"], invoice_number=legacy.get("invoice_number") or None,
        )

    details = _index(records, "registration.registrationdetails")
    by_membership = defaultdict(list)
    for key, legacy in _index(original, "registration.registrationdetails").items():
        detail = details[key]
        membership = memberships[str(legacy["membership_id"])]
        variant = variants.get(str(legacy.get("product_variant_id")), {})
        product = products.get(str(variant.get("product_id")), {})
        description = " · ".join(part for part in (product.get("name"), variant.get("name")) if part)
        detail.update(
            member_id=membership["member_id"], base_price=legacy["price"], excluded=legacy.get("excluded_from_invoice", False),
            product_id=variant.get("product_id"), description=description or legacy["entry_kind"].capitalize(),
        )
        by_membership[str(legacy["membership_id"])].append(detail)

    legacy_batches = _index(original, "registration.registrationbatch")
    legacy_details = _index(original, "registration.registrationdetails")
    history_batches = set()
    for items in by_membership.values():
        if len(items) == 1:
            continue
        invoice_ids = {str(item["batch_id"]) for item in items}
        history_batches.update(invoice_ids)
        # The membership ledger carries the balance for the whole season. Keep it on one
        # confirmed invoice if available, and retain the source line items on every invoice.
        items.sort(key=lambda item: (
            not batches[str(item["batch_id"])]["confirmed_at"], item["entry_kind"] != "player",
            item["created"], item["id"],
        ))
        primary = items[0]
        active = [item for item in items if not item["excluded"]]
        primary.update(
            base_price=str(sum((Decimal(item["base_price"]) for item in active), Decimal(0))),
            discount_amount=str(sum((Decimal(item["discount_amount"]) for item in active), Decimal(0))),
            description="; ".join(dict.fromkeys(item["description"] for item in items))[:255],
            excluded=not active,
        )
        for item in items[1:]:
            del details[str(item["id"])]

    if history_batches:
        warnings.append("Original lines on registrations with repeated memberships are shown in Finance as legacy invoice history. Payments follow the member's season balance.")
        details_by_batch = defaultdict(list)
        for row in legacy_details.values():
            batch_id = str(row["batch_id"])
            if batch_id not in history_batches:
                continue
            member = members[str(memberships[str(row["membership_id"])]["member_id"])]
            variant = variants.get(str(row.get("product_variant_id")), {})
            product = products.get(str(variant.get("product_id")), {})
            label = " · ".join(part for part in (product.get("name"), variant.get("name")) if part)
            details_by_batch[batch_id].append({
                **row,
                "member_id": str(member["id"]),
                "member": " ".join(part for part in (member.get("first_name"), member.get("last_name")) if part),
                "description": label or row["entry_kind"].capitalize(),
            })
        for batch_id in history_batches:
            original_batch = legacy_batches[batch_id]
            batches[batch_id]["legacy_invoice_history"] = {
                "subtotal": str(original_batch.get("subtotal", "0.00")),
                "discount_amount": str(original_batch.get("discount_amount", "0.00")),
                "manual_discount_amount": str(original_batch.get("manual_discount_amount", "0.00")),
                "total": str(original_batch.get("total", "0.00")),
                "lines": details_by_batch[batch_id],
            }
    if details:
        records["registration.registrationdetails"] = [_row(detail) for detail in details.values()]


def _shop_production(source, records, products):
    orders = _index(records, "shop.order")
    for order in orders.values():
        if order.get("production_status") == "in_production":
            order["production_status"] = "ordered"
    ordered_lines = defaultdict(list)
    for line in _index(records, "shop.orderline").values():
        status = line.get("production_status")
        if status == "in_production":
            line.update(production_status="ordered", ordered_at=line["modified"])
            product = products[str(line["product_id"])]
            order = orders[str(line["order_id"])]
            if product["product_type"] == "merchandise" and not product.get("inventory_managed") and order["fulfillment_status"] != "cancelled":
                ordered_lines[str(line["order_id"])].append(line)
        elif status == "received":
            line.update(received_quantity=line["quantity"], received_at=line["modified"])
    for order_id, lines in ordered_lines.items():
        order = orders[order_id]
        batch_id = _id(source, "productionbatch", order_id)
        records.setdefault("shop.productionbatch", []).append(_row({
            "id": batch_id, "number": f"LEGACY-{order['number'] or order_id}", "created": order["created"],
            "modified": order["modified"], "ordered_at": min(line["ordered_at"] for line in lines), "status": "ordered",
        }))
        for line in lines:
            line["production_batch_id"] = batch_id


def convert_v1(manifest):
    source = manifest["club"]
    original = manifest["records"]
    allowed = tenant_models()
    records = {}
    warnings = []
    for old_label, rows in original.items():
        if old_label in LEGACY_ROLES:
            continue
        label = MODEL_MOVES.get(old_label, old_label)
        if label not in allowed:
            raise BackupError(f"Unsupported v1 backup model: {old_label}.")
        fields = _fields(allowed[label])
        interest_only = label in {"events.refereesignup", "events.officialsignup"}
        records[label] = [
            {"fields": {name: value for name, value in row["fields"].items() if name in fields}, "many": row.get("many", {})}
            for row in rows
            if not interest_only or row["fields"].get("status") == "accepted"
        ]

    members = _index(records, "members.member")
    for membership in sorted(_index(original, "club.clubmembership").values(), key=lambda row: row["created"]):
        if membership.get("license"):
            members[str(membership["member_id"])]["license_number"] = membership["license"][:50]

    for row in records.get("members.familymembership", ()):
        values = row["fields"]
        values.update(id=_id(source, "familymembership", values["id"]), created=manifest["exported_at"], modified=manifest["exported_at"])

    categories = _index(original, "shop.productcategory")
    for row in records.get("shop.productcategory", ()):
        values = row["fields"]
        original_category = categories[str(values["id"])]
        key = original_category.get("registration_kind") or values["name"].lower()
        if key in ("player", "volunteer"):
            values.update(system_key=key, name=key.title())

    products = _index(records, "shop.product")
    for key, values in _index(original, "shop.product").items():
        products[key]["early_bird_deadline"] = values.get("early_bird_discount_deadline") if values.get("early_bird_discount_enabled") else None
        if values.get("image"):
            records.setdefault("shop.productimage", []).append(_row({
                "id": _id(source, "productimage", key), "product_id": key, "image": values["image"], "is_main": True,
                "created": values["created"], "modified": values["modified"],
            }))
    _registrations(source, records, original, warnings)
    _shop_production(source, records, products)

    accounts = {str(account["id"]): {**account, "roles": []} for account in manifest["accounts"]}
    role_defs = {"member": ("Member", []), **ROLE_DEFINITIONS}
    for label, role in LEGACY_ROLES.items():
        for values in _index(original, label).values():
            member = members[str(values["member_id"])]
            account = accounts.get(str(member.get("user_id")))
            if account:
                account["enrolled"] = True
                account["roles"].append(role or ROLE_CODES[values["role"]])
    roles = [{"slug": slug, "name": name, "permissions": codes} for slug, (name, codes) in role_defs.items()]
    fields = {name: value for name, value in source["fields"].items() if name in CLUB_FIELDS}
    fields["shop_enabled"] = bool(source["fields"].get("shop_open"))
    if source.get("registration_open") is not None:
        records["registration.registrationsettings"] = [_row({
            "id": _id(source, "registrationsettings", 1), "created": manifest["exported_at"], "modified": manifest["exported_at"],
            "is_open": source["registration_open"], "payment_instructions": fields.get("payment_instructions", ""),
        })]
    features = {key: value for key, value in manifest.get("features", {}).items() if key in FEATURE_CATALOG}
    return {
        **manifest, "club": {**source, "fields": fields},
        "accounts": list(accounts.values()), "roles": roles, "records": records, "features": features,
    }, warnings
