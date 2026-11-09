"""Telling the purchaser (and, for a child, the guardians) about their order: inbox + push, category "shop".

``dispatch(kind, order_id)`` queues a Celery task (tenant-aware); ``send(kind, order)`` does the work. Each recipient gets the
text in their own language (``Member.language``; empty/unknown means the club's own, Dutch). Kinds: ``placed``, ``ready``,
``cancelled``, ``payment_received``, ``production`` (all made-to-order items of the order are now ordered from the supplier) and
``production_partial`` (some of them; the others wait for another batch). The production kinds carry ``extra['items']``."""

import logging

logger = logging.getLogger(__name__)

CATEGORY = "shop"
DEFAULT_LANGUAGE = "nl"

TEXTS = {
    "nl": {
        "placed": ("Bestelling {number} geplaatst", "Je bestelling is geregistreerd. Totaal: EUR {total}."),
        "ready": ("Bestelling {number} ligt klaar", "Je bestelling kan worden opgehaald. {instructions}"),
        "cancelled": ("Bestelling {number} geannuleerd", "Je bestelling werd geannuleerd."),
        "payment_received": ("Betaling ontvangen voor {number}", "Bedankt, we hebben je betaling ontvangen."),
        "production": ("Bestelling {number} is in productie", "Je bestelling werd naar de leverancier gestuurd en wordt nu gemaakt: {items}. We verwittigen je zodra hij klaarligt."),
        "production_partial": (
            "Een deel van bestelling {number} is in productie",
            "Deze artikelen worden nu gemaakt: {items}. "
            "Voor de rest van je bestelling laten we je later weten wanneer ze in productie gaat. We verwittigen je zodra alles klaarligt.",
        ),
    },
    "en": {
        "placed": ("Order {number} placed", "Your order is registered. Total: EUR {total}."),
        "ready": ("Order {number} is ready for pickup", "Your order is ready to collect. {instructions}"),
        "cancelled": ("Order {number} cancelled", "Your order was cancelled."),
        "payment_received": ("Payment received for {number}", "Thank you, we received your payment."),
        "production": ("Order {number} is in production", "Your order was sent to the supplier and is now being made: {items}. We will let you know when it is ready."),
        "production_partial": ("Part of order {number} is in production", "These items are now being made: {items}. We will let you know when the rest of your order goes into production. We will tell you when everything is ready."),
    },
    "fr": {
        "placed": ("Commande {number} enregistree", "Votre commande est enregistree. Total : EUR {total}."),
        "ready": ("Commande {number} prete", "Votre commande peut etre retiree. {instructions}"),
        "cancelled": ("Commande {number} annulee", "Votre commande a ete annulee."),
        "payment_received": ("Paiement recu pour {number}", "Merci, nous avons recu votre paiement."),
        "production": ("Commande {number} en production", "Votre commande a ete envoyee au fournisseur et est en cours de fabrication : {items}. Nous vous previendrons quand elle sera prete."),
        "production_partial": (
            "Une partie de la commande {number} est en production",
            "Ces articles sont en cours de fabrication : {items}. "
            "Nous vous previendrons quand le reste de votre commande sera en production. Nous vous previendrons quand tout sera pret.",
        ),
    },
}


PAYMENT_LABEL = {"nl": "Betalen:", "en": "How to pay:", "fr": "Comment payer :"}


def _how_to_pay(extra: dict | None) -> str:
    """The payment instructions to tell: the text given with the notification (edited in the pickup workflow, possibly empty on
    purpose), else the club-level default."""
    from shop.services import defaults

    if extra and extra.get("payment_instructions") is not None:
        return str(extra["payment_instructions"]).strip()
    return defaults.payment_instructions().strip()


def _lang(code) -> str:
    code = (code or "").split("-")[0].lower()
    return code if code in TEXTS else DEFAULT_LANGUAGE


def send(kind: str, order, extra: dict | None = None) -> int:
    from events.services.attendance import current_club
    from members.services import recipients
    from notifications import inbox

    club = current_club()
    if club is None or kind not in TEXTS["nl"]:
        return 0
    member = order.purchaser
    language = _lang(member.language)
    told = 0
    for user, child in recipients.recipients(member):
        # a guardian reads in their own language
        from members.models import Member

        own = Member.objects.filter(user=user).values_list("language", flat=True).first() if child else member.language
        title, body = TEXTS[_lang(own or language)][kind]
        values = {"number": order.number, "total": order.total, "instructions": order.pickup_instructions, "items": (extra or {}).get("items", "")}
        title, body = title.format(**values), body.format(**values).strip()
        if kind in ("placed", "ready") and order.payment_status != "paid":
            how_to_pay = _how_to_pay(extra)
            if how_to_pay:
                body = f"{body}\n\n{PAYMENT_LABEL[_lang(own or language)]}\n{how_to_pay}"
        if child is not None:
            title = recipients.on_behalf_title(title, child)
        inbox.notify(user, club, title=title, body=body, href=f"/shop/orders/{order.pk}", push=True, category=CATEGORY)
        told += 1
    return told


def dispatch(kind: str, order_id, extra: dict | None = None) -> None:
    from shop import tasks

    try:
        tasks.notify_order.delay(kind, str(order_id), extra or {})
    except Exception:  # a broker hiccup must not fail the request
        logger.exception("shop.enqueue_failed %s", kind)
