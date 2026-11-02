import mimetypes

from django.http import FileResponse, Http404
from django.utils.cache import patch_vary_headers
from django.views import View

from api.auth import MFARequired, TokenAuth

from .models import Member
from .services.photos import can_view_member_photo

# Pictures are user uploads: never let a browser run or embed anything but the image itself.
MEDIA_CSP = "default-src 'none'; img-src 'self'; sandbox"


def request_user(request):
    """The signed-in user: the session cookie, else an ``X-Session-Token`` header (what the SvelteKit apps send through
    their proxy, since an ``<img>`` tag cannot send headers itself). ``None`` when anonymous, or when the account would
    not pass the API's own MFA rule."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return user
    try:
        return TokenAuth()(request)
    except MFARequired:
        return None


class MemberPhotoView(View):
    """Streams a member picture from private storage to whoever ``can_view_member_photo`` allows. Staff authority comes
    from the club the request is for (``request.tenant``), so it only counts on that club's own host. Everyone else
    gets a 404, which does not confirm that the member or the picture exists."""

    #: ``player_photo`` / ``profile_photo``, or ``None`` for the effective picture.
    field: str | None = None

    def get(self, request, pk):
        user = request_user(request)
        member = Member.objects.filter(pk=pk).first()
        if member is None:
            raise Http404("No such photo.")
        photo_file = getattr(member, self.field) if self.field else member.photo
        if not photo_file or not can_view_member_photo(user, member, getattr(request, "tenant", None), field=self.field):
            raise Http404("No such photo.")
        try:
            photo = photo_file.open("rb")
        except FileNotFoundError as error:
            raise Http404("No such photo.") from error

        content_type = mimetypes.guess_type(photo_file.name)[0] or "application/octet-stream"
        if not content_type.startswith("image/") or content_type == "image/svg+xml":
            content_type = "application/octet-stream"
        response = FileResponse(photo, content_type=content_type)
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = MEDIA_CSP
        patch_vary_headers(response, ["Cookie", "X-Session-Token"])
        return response
