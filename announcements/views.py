from django.http import JsonResponse
from django.views import View

from .services import consume_for


class PendingAnnouncementView(View):
    """Polled by a small inline script in both management/base.html and
    mobile/base.html on every page load -- deliberately a plain fetch rather
    than a context processor, so "seen" is only ever recorded once the client
    has actually run JS to receive it (a context processor would fire on every
    server render, including an htmx partial that never shows the dialog at
    all). Mounted once at the project root (rosterchief/urls.py), not under
    either app's own urls.py, since management and mobile share it verbatim.
    """

    def get(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({})

        announcement = consume_for(request.user, getattr(request, "club", None))
        if announcement is None:
            return JsonResponse({})

        return JsonResponse({"title": announcement.title, "message": announcement.message})
