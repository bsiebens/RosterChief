"""A member's preferred language: saved when they pick one, applied when the browser has no language cookie.

The cookie stays what Django's LocaleMiddleware reads first (it is per browser, and anonymous visitors have
nothing else). ``Member.language`` is the durable copy: it survives a cleared cookie jar, follows the
account to other devices, and is what push notifications are rendered in (mobile.services.push).
"""

from django.conf import settings
from django.utils import translation
from django.views.decorators.http import require_POST
from django.views.i18n import set_language as django_set_language

from members.services.lookup import get_request_member


@require_POST
def set_language(request):
    """Django's set_language (cookie + redirect), plus saving the choice on the signed-in member."""
    response = django_set_language(request)
    code = request.POST.get("language", "")
    member = getattr(request.user, "member", None) if request.user.is_authenticated else None
    if member is not None and code in dict(settings.LANGUAGES) and member.language != code:
        member.language = code
        member.save(update_fields=["language"])
    return response


class MemberLanguageMiddleware:
    """Without a language cookie, a signed-in member's saved language wins over the Accept-Language guess."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.LANGUAGE_COOKIE_NAME not in request.COOKIES and request.user.is_authenticated:
            member = get_request_member(request)
            if member is not None and member.language:
                translation.activate(member.language)
                request.LANGUAGE_CODE = member.language
        return self.get_response(request)
