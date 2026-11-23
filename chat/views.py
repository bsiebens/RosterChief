import mimetypes

from django.http import FileResponse, Http404
from django.views import View
from django_tenants.utils import get_public_schema_name

from chat.models import MessageAttachment
from chat.services import access
from members.models import Member
from members.services import access as member_access
from members.views import request_user


class AttachmentView(View):
    """Streams a chat attachment from private storage to people who may see its chat (participants, parents reading along, moderators).
    Anybody else gets a 404 that does not confirm the file exists. Served inline with its checked type and ``nosniff``."""

    def get(self, request, pk):
        tenant = getattr(request, "tenant", None)
        user = request_user(request)
        features = getattr(request, "features", None)
        if user is None or tenant is None or tenant.schema_name == get_public_schema_name() or features is None or not features.has("chat"):
            raise Http404("No such file.")
        attachment = MessageAttachment.objects.select_related("conversation").filter(pk=pk, message__isnull=False, message__deleted_at__isnull=True).first()
        me = Member.objects.filter(user=user).first()
        if attachment is None or me is None:
            raise Http404("No such file.")
        if not (access.can_see(me, attachment.conversation) or member_access.can_moderate_chat(user)):
            raise Http404("No such file.")
        try:
            handle = attachment.file.open("rb")
        except FileNotFoundError as error:
            raise Http404("No such file.") from error
        safe = attachment.content_type == "application/pdf" or attachment.content_type.startswith("image/")
        response = FileResponse(handle, content_type=attachment.content_type if safe else (mimetypes.guess_type(attachment.name)[0] or "application/octet-stream"))
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Content-Type-Options"] = "nosniff"
        return response
