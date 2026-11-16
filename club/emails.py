"""Email sent to a member once they are granted a login.

It is an *automated* email: ``rosterchief.mail.send_message`` drops it while the platform's email pause is on
(unlike password-reset email, which is exempt). A newly created account gets a link to set its password straight
away (the same link and page as "forgot password"); someone who already had an account just gets the sign-in link.
"""

import email.policy
import logging
from pathlib import Path

from allauth.account import app_settings as allauth_settings
from allauth.account.adapter import get_adapter
from allauth.account.utils import user_pk_to_url_str
from allauth.core import context
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from rosterchief.mail import send_message

log = logging.getLogger(__name__)

DEFAULT_PRIMARY, DEFAULT_SECONDARY = "#0f172a", "#e4002b"

TEXTS = {
    "en": {
        "subject": "Your {club} account is ready",
        "hello": "Hi {name},",
        "created": "{club} has created an account for you. Set a password to sign in and get started.",
        "existing": "{club} has given your existing RosterChief account access to its members area. Sign in with your usual email address and password.",
        "button_set": "Set your password",
        "button_login": "Sign in",
        "link_note": "The link above lets you choose a password straight away. Your email address is already verified.",
        "login_line": "Sign in at",
        "email_line": "Your login is",
        "footer": "You received this email because {club} gave you access. If you didn't expect it, you can ignore it.",
    },
    "nl": {
        "subject": "Je account bij {club} is klaar",
        "hello": "Hallo {name},",
        "created": "{club} heeft een account voor je aangemaakt. Stel een wachtwoord in om aan de slag te gaan.",
        "existing": "{club} heeft je bestaande RosterChief-account toegang gegeven tot de ledenomgeving. Log in met je gewone e-mailadres en wachtwoord.",
        "button_set": "Stel je wachtwoord in",
        "button_login": "Inloggen",
        "link_note": "Via bovenstaande link kies je meteen een wachtwoord. Je e-mailadres is al geverifieerd.",
        "login_line": "Inloggen kan op",
        "email_line": "Je login is",
        "footer": "Je ontvangt deze e-mail omdat {club} je toegang gaf. Verwachtte je dit niet, dan mag je hem negeren.",
    },
    "fr": {
        "subject": "Votre compte {club} est prêt",
        "hello": "Bonjour {name},",
        "created": "{club} a créé un compte pour vous. Définissez un mot de passe pour vous connecter et commencer.",
        "existing": "{club} a donné à votre compte RosterChief existant l’accès à son espace membres. Connectez-vous avec votre adresse e-mail et votre mot de passe habituels.",
        "button_set": "Définir votre mot de passe",
        "button_login": "Se connecter",
        "link_note": "Le lien ci-dessus vous permet de choisir un mot de passe tout de suite. Votre adresse e-mail est déjà vérifiée.",
        "login_line": "Connectez-vous sur",
        "email_line": "Votre identifiant est",
        "footer": "Vous recevez cet e-mail car {club} vous a donné accès. Si vous ne l’attendiez pas, vous pouvez l’ignorer.",
    },
}


AUTH_TEXTS = {
    "en": {
        "password_reset_key": {
            "subject": "Reset your {club} password",
            "heading": "Reset your password",
            "intro": "We received a request to reset the password of your account. Click the button to choose a new one. If you didn't ask for this, you can safely ignore this email.",
            "button": "Choose a new password",
            "link_note": "Or copy this link into your browser:",
        },
        "email_confirmation": {
            "subject": "Confirm your email address for {club}",
            "heading": "Confirm your email address",
            "intro": "Please confirm that this is your email address so you can sign in to {club}.",
            "button": "Confirm email address",
            "link_note": "Or copy this link into your browser:",
        },
        "password_changed": {
            "subject": "Your {club} password was changed",
            "heading": "Your password was changed",
            "intro": "The password of your account was just changed.",
        },
        "password_set": {
            "subject": "A password was set on your {club} account",
            "heading": "Your password was set",
            "intro": "A password was just set on your account.",
        },
        "password_reset": {
            "subject": "Your {club} password was reset",
            "heading": "Your password was reset",
            "intro": "The password of your account was just reset.",
        },
        "email_changed": {
            "subject": "Your {club} email address was changed",
            "heading": "Your email address was changed",
            "intro": "The email address on your account was just changed.",
        },
        "email_deleted": {
            "subject": "An email address was removed from your {club} account",
            "heading": "An email address was removed",
            "intro": "An email address was just removed from your account.",
        },
        "email_confirm": {
            "subject": "Your email address for {club} is confirmed",
            "heading": "Your email address is confirmed",
            "intro": "Thanks, your email address is now confirmed.",
        },
        "account_already_exists": {
            "subject": "You already have a {club} account",
            "heading": "You already have an account",
            "intro": "Someone, possibly you, tried to sign up with an email address that already has an account. If you forgot your password, you can choose a new one.",
            "button": "Reset my password",
            "link_note": "Or copy this link into your browser:",
        },
        "unknown_account": {
            "subject": "We don't know this email address at {club}",
            "heading": "No account found",
            "intro": "Someone, possibly you, tried to access an account with this email address, but we have no record of it. You can safely ignore this email if it wasn't you. If it was, you can sign up using the button below.",
            "button": "Create an account",
            "link_note": "Or copy this link into your browser:",
        },
        "login_code": {
            "subject": "Your {club} sign-in code",
            "heading": "Your sign-in code",
            "intro": "Use this code to finish signing in to {club}.",
        },
        "password_reset_code": {
            "subject": "Your {club} password reset code",
            "heading": "Your password reset code",
            "intro": "Use this code to reset your {club} password.",
        },
        "common": {
            "security": "If you don't recognise this change, secure your account right away by resetting your password and contact {club}.",
            "ip": "IP address",
            "browser": "Browser",
            "date": "Date",
            "from": "From",
            "to": "To",
            "removed": "Removed address",
            "email": "Email address",
            "code_note": "Enter this code in your open browser window. If you didn't request it, you can safely ignore this email.",
        },
        "footer": "This is an automated message from {club}.",
    },
    "nl": {
        "password_reset_key": {
            "subject": "Stel je {club}-wachtwoord opnieuw in",
            "heading": "Wachtwoord opnieuw instellen",
            "intro": "We ontvingen een aanvraag om het wachtwoord van je account opnieuw in te stellen. Klik op de knop om een nieuw wachtwoord te kiezen. Heb je dit niet gevraagd, dan mag je deze e-mail negeren.",
            "button": "Kies een nieuw wachtwoord",
            "link_note": "Of kopieer deze link naar je browser:",
        },
        "email_confirmation": {
            "subject": "Bevestig je e-mailadres voor {club}",
            "heading": "Bevestig je e-mailadres",
            "intro": "Bevestig dat dit je e-mailadres is zodat je kunt inloggen bij {club}.",
            "button": "E-mailadres bevestigen",
            "link_note": "Of kopieer deze link naar je browser:",
        },
        "password_changed": {
            "subject": "Je {club}-wachtwoord is gewijzigd",
            "heading": "Je wachtwoord is gewijzigd",
            "intro": "Het wachtwoord van je account is zonet gewijzigd.",
        },
        "password_set": {
            "subject": "Er is een wachtwoord ingesteld voor je {club}-account",
            "heading": "Je wachtwoord is ingesteld",
            "intro": "Er is zonet een wachtwoord ingesteld voor je account.",
        },
        "password_reset": {
            "subject": "Je {club}-wachtwoord is opnieuw ingesteld",
            "heading": "Je wachtwoord is opnieuw ingesteld",
            "intro": "Het wachtwoord van je account is zonet opnieuw ingesteld.",
        },
        "email_changed": {
            "subject": "Je e-mailadres bij {club} is gewijzigd",
            "heading": "Je e-mailadres is gewijzigd",
            "intro": "Het e-mailadres van je account is zonet gewijzigd.",
        },
        "email_deleted": {
            "subject": "Een e-mailadres is verwijderd van je {club}-account",
            "heading": "Een e-mailadres is verwijderd",
            "intro": "Er is zonet een e-mailadres van je account verwijderd.",
        },
        "email_confirm": {
            "subject": "Je e-mailadres voor {club} is bevestigd",
            "heading": "Je e-mailadres is bevestigd",
            "intro": "Bedankt, je e-mailadres is nu bevestigd.",
        },
        "account_already_exists": {
            "subject": "Je hebt al een account bij {club}",
            "heading": "Je hebt al een account",
            "intro": "Iemand, mogelijk jijzelf, probeerde zich in te schrijven met een e-mailadres dat al een account heeft. Ben je je wachtwoord vergeten, dan kun je een nieuw kiezen.",
            "button": "Mijn wachtwoord opnieuw instellen",
            "link_note": "Of kopieer deze link naar je browser:",
        },
        "unknown_account": {
            "subject": "We kennen dit e-mailadres niet bij {club}",
            "heading": "Geen account gevonden",
            "intro": (
                "Iemand, mogelijk jijzelf, probeerde toegang te krijgen tot een account met dit e-mailadres, maar we hebben er geen gegevens van. "
                "Was jij het niet, dan mag je deze e-mail negeren. Was jij het wel, dan kun je je inschrijven via de knop hieronder."
            ),
            "button": "Account aanmaken",
            "link_note": "Of kopieer deze link naar je browser:",
        },
        "login_code": {
            "subject": "Je {club}-inlogcode",
            "heading": "Je inlogcode",
            "intro": "Gebruik deze code om het inloggen bij {club} af te ronden.",
        },
        "password_reset_code": {
            "subject": "Je {club}-code voor wachtwoordherstel",
            "heading": "Je code voor wachtwoordherstel",
            "intro": "Gebruik deze code om je {club}-wachtwoord opnieuw in te stellen.",
        },
        "common": {
            "security": "Herken je deze wijziging niet? Beveilig je account dan meteen door je wachtwoord opnieuw in te stellen en neem contact op met {club}.",
            "ip": "IP-adres",
            "browser": "Browser",
            "date": "Datum",
            "from": "Van",
            "to": "Naar",
            "removed": "Verwijderd adres",
            "email": "E-mailadres",
            "code_note": "Vul deze code in je geopende browservenster in. Heb je er niet om gevraagd, dan mag je deze e-mail negeren.",
        },
        "footer": "Dit is een automatisch bericht van {club}.",
    },
    "fr": {
        "password_reset_key": {
            "subject": "Réinitialisez votre mot de passe {club}",
            "heading": "Réinitialiser votre mot de passe",
            "intro": "Nous avons reçu une demande de réinitialisation du mot de passe de votre compte. Cliquez sur le bouton pour en choisir un nouveau. Si vous n’êtes pas à l’origine de cette demande, ignorez simplement cet e-mail.",
            "button": "Choisir un nouveau mot de passe",
            "link_note": "Ou copiez ce lien dans votre navigateur :",
        },
        "email_confirmation": {
            "subject": "Confirmez votre adresse e-mail pour {club}",
            "heading": "Confirmez votre adresse e-mail",
            "intro": "Veuillez confirmer que cette adresse e-mail est la vôtre pour pouvoir vous connecter à {club}.",
            "button": "Confirmer l’adresse e-mail",
            "link_note": "Ou copiez ce lien dans votre navigateur :",
        },
        "password_changed": {
            "subject": "Votre mot de passe {club} a été modifié",
            "heading": "Votre mot de passe a été modifié",
            "intro": "Le mot de passe de votre compte vient d’être modifié.",
        },
        "password_set": {
            "subject": "Un mot de passe a été défini sur votre compte {club}",
            "heading": "Votre mot de passe a été défini",
            "intro": "Un mot de passe vient d’être défini sur votre compte.",
        },
        "password_reset": {
            "subject": "Votre mot de passe {club} a été réinitialisé",
            "heading": "Votre mot de passe a été réinitialisé",
            "intro": "Le mot de passe de votre compte vient d’être réinitialisé.",
        },
        "email_changed": {
            "subject": "Votre adresse e-mail {club} a été modifiée",
            "heading": "Votre adresse e-mail a été modifiée",
            "intro": "L’adresse e-mail de votre compte vient d’être modifiée.",
        },
        "email_deleted": {
            "subject": "Une adresse e-mail a été supprimée de votre compte {club}",
            "heading": "Une adresse e-mail a été supprimée",
            "intro": "Une adresse e-mail vient d’être supprimée de votre compte.",
        },
        "email_confirm": {
            "subject": "Votre adresse e-mail pour {club} est confirmée",
            "heading": "Votre adresse e-mail est confirmée",
            "intro": "Merci, votre adresse e-mail est maintenant confirmée.",
        },
        "account_already_exists": {
            "subject": "Vous avez déjà un compte {club}",
            "heading": "Vous avez déjà un compte",
            "intro": "Quelqu’un, peut-être vous, a essayé de s’inscrire avec une adresse e-mail qui a déjà un compte. Si vous avez oublié votre mot de passe, vous pouvez en choisir un nouveau.",
            "button": "Réinitialiser mon mot de passe",
            "link_note": "Ou copiez ce lien dans votre navigateur :",
        },
        "unknown_account": {
            "subject": "Nous ne connaissons pas cette adresse e-mail chez {club}",
            "heading": "Aucun compte trouvé",
            "intro": (
                "Quelqu’un, peut-être vous, a essayé d’accéder à un compte avec cette adresse e-mail, mais nous n’en avons aucune trace. "
                "Vous pouvez ignorer cet e-mail si ce n’était pas vous. Sinon, vous pouvez vous inscrire avec le bouton ci-dessous."
            ),
            "button": "Créer un compte",
            "link_note": "Ou copiez ce lien dans votre navigateur :",
        },
        "login_code": {
            "subject": "Votre code de connexion {club}",
            "heading": "Votre code de connexion",
            "intro": "Utilisez ce code pour terminer votre connexion à {club}.",
        },
        "password_reset_code": {
            "subject": "Votre code de réinitialisation {club}",
            "heading": "Votre code de réinitialisation",
            "intro": "Utilisez ce code pour réinitialiser votre mot de passe {club}.",
        },
        "common": {
            "security": "Si vous ne reconnaissez pas cette modification, sécurisez immédiatement votre compte en réinitialisant votre mot de passe et contactez {club}.",
            "ip": "Adresse IP",
            "browser": "Navigateur",
            "date": "Date",
            "from": "De",
            "to": "Vers",
            "removed": "Adresse supprimée",
            "email": "Adresse e-mail",
            "code_note": "Saisissez ce code dans la fenêtre de votre navigateur. Si vous ne l’avez pas demandé, ignorez simplement cet e-mail.",
        },
        "footer": "Ceci est un message automatique de {club}.",
    },
}
AUTH_KINDS = {
    "account/email/password_reset_key": "password_reset_key",
    "account/email/email_confirmation": "email_confirmation",
    "account/email/email_confirmation_signup": "email_confirmation",
    **{f"account/email/{kind}": kind for kind in ("password_changed", "password_set", "password_reset", "email_changed", "email_deleted", "email_confirm", "account_already_exists", "unknown_account", "login_code", "password_reset_code")},
}


PLATFORM_LOGO_CID = "rosterchief-logo"
PLATFORM_LOGO_PATH = Path(__file__).parent / "email_assets" / "rosterchief-logo.png"


def attach_platform_logo(message) -> None:
    """Embed the RosterChief logo for ``cid:rosterchief-logo`` (emails from the platform itself carry it in the header).
    A no-op when the HTML body does not reference it. Django has no API for inline images, so the built MIME message
    is post-processed: the HTML part becomes multipart/related with the image next to it."""
    html = next((body for body, mimetype in getattr(message, "alternatives", []) if mimetype == "text/html"), "")
    if f"cid:{PLATFORM_LOGO_CID}" not in html:
        return
    build = message.message

    def message_with_logo(*, policy=email.policy.default):
        msg = build(policy=policy)
        part = msg.get_body(("html",))
        if part is not None:
            part.add_related(PLATFORM_LOGO_PATH.read_bytes(), "image", "png", cid=f"<{PLATFORM_LOGO_CID}>", filename="rosterchief.png")
        return msg

    message.message = message_with_logo


def branding_context(request) -> dict:
    """Name, logo and colours for the email header: the club the request is for, else the platform itself."""
    from django.utils import translation

    club = getattr(request, "tenant", None) if request is not None else None
    is_club = club is not None and club.schema_name != "public"
    lang = (translation.get_language() or "en")[:2]
    lang = lang if lang in TEXTS else "en"
    if not is_club:
        return {"club_name": "RosterChief", "club_initials": "RC", "logo_url": f"cid:{PLATFORM_LOGO_CID}", "primary": DEFAULT_PRIMARY, "primary_content": "#ffffff", "secondary": DEFAULT_SECONDARY, "secondary_content": "#ffffff", "lang": lang}
    return {
        "club_name": club.name,
        "club_initials": "".join(w[0] for w in club.name.split()[:2]).upper(),
        "logo_url": request.build_absolute_uri(club.logo.url) if club.logo else "",
        "primary": club.primary_color or DEFAULT_PRIMARY,
        "primary_content": (club.primary_content_color or "#ffffff") if club.primary_color else "#ffffff",
        "secondary": club.secondary_color or DEFAULT_SECONDARY,
        "secondary_content": (club.secondary_content_color or "#ffffff") if club.secondary_color else "#ffffff",
        "lang": lang,
    }


def auth_email_context(request, template_prefix: str) -> dict:
    """Extra template context for allauth's emails (password reset, email confirmation): branding plus the texts."""
    ctx = branding_context(request)
    kind = AUTH_KINDS.get(template_prefix)
    if kind is None:
        return ctx
    texts = AUTH_TEXTS[ctx["lang"]]
    name = ctx["club_name"]
    common = {k: v.format(club=name) for k, v in texts["common"].items()}
    ctx["t"] = {**common, **{k: v.format(club=name) for k, v in texts[kind].items()}, "footer": texts["footer"].format(club=name)}
    return ctx


def _set_password_url(request, user) -> str:
    """The "reset password" link allauth would email, built here so it opens the same page (and logs nobody in)."""
    token = allauth_settings.PASSWORD_RESET_TOKEN_GENERATOR().make_token(user)
    key = f"{user_pk_to_url_str(user)}-{token}"
    with context.request_context(request):
        return get_adapter().get_reset_password_from_key_url(key)


def _login_url(request) -> str:
    from django.conf import settings

    app = "management" if request.tenant.schema_name != "public" else None
    base = settings.FRONTEND_URLS.get(app or "") or ""
    return (base + "/login") if base else request.build_absolute_uri("/login")


def build_context(request, club, member, user, *, user_created: bool, lang: str | None = None, set_password_url: str = "", login_url: str = "") -> dict:
    lang = lang if lang in TEXTS else (member.language if member.language in TEXTS else "en")
    texts = {k: v.format(club=club.name, name=member.first_name or user.email) if "{" in v else v for k, v in TEXTS[lang].items()}
    branding = branding_context(request)
    branding["lang"] = lang
    return {
        **branding,
        "t": texts,
        "login_email": user.email,
        "user_created": user_created,
        "set_password_url": set_password_url if user_created else "",
        "login_url": login_url,
        "lang": lang,
    }


def render_login_granted(ctx: dict) -> tuple[str, str, str]:
    """(subject, text body, html body)."""
    subject = TEXTS[ctx["lang"]]["subject"].format(club=ctx["club_name"])
    return subject, render_to_string("club/email/login_granted.txt", ctx), render_to_string("club/email/login_granted.html", ctx)


def send_login_granted_email(request, club, member, user, *, user_created: bool) -> bool:
    """Email ``user`` that they now have a login. Returns False when nothing was sent (email paused, or it failed:
    a mail problem must never undo the grant)."""
    try:
        ctx = build_context(
            request,
            club,
            member,
            user,
            user_created=user_created,
            set_password_url=_set_password_url(request, user) if user_created else "",
            login_url=_login_url(request),
        )
        subject, text, html = render_login_granted(ctx)
        message = EmailMultiAlternatives(subject, text, to=[user.email])
        message.attach_alternative(html, "text/html")
        return send_message(message)
    except Exception:
        log.exception("Could not send the login email to %s", user.email)
        return False
