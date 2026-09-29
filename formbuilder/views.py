from django.contrib.auth.views import redirect_to_login
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext_lazy as _
from django.views import View
from waffle import flag_is_active

from club.services.access import can_manage_forms
from controlpanel.messages import notify
from members.models import Member
from members.views import ClubScopedPublicMixin
from rosterchief import throttle
from rosterchief.client_ip import client_ip

from .models import Answer, FormSend
from .services.form_factory import build_form
from .services.submission import FormSubmissionError, submit_form

#: Cap on recorded responses through one public link, per client IP. Only a
#: response that is actually saved counts (an invalid POST saves nothing).
PUBLIC_FORM_IP_RATE = throttle.Rate("formbuilder:public:ip", limit=10, window=60 * 60)

#: Honeypot input rendered hidden on public_form.html. The dot keeps it from
#: ever colliding with a Field.key (a slug), whatever the form's questions are.
HONEYPOT_FIELD = "contact.website"


class PublicFormFillView(ClubScopedPublicMixin, View):
    """The public, unauthenticated form-fill page -- linkable from wherever a
    club shares it (their own site, email, social...), same "public, club-
    scoped, no login" shape as registration.views.RegistrationView. Reached
    by FormSend.public_token, only ever looked up with is_public=True (see
    get_send) -- opting a send into having a link at all is a deliberate,
    separate choice from whether an anonymous submission is actually let
    through, which is still formbuilder.services.submission.submit_form's
    own call (anonymous only for a public send whose Form doesn't require
    sign-in). Responses through the link are capped per client IP per send
    (PUBLIC_FORM_IP_RATE), and a hidden honeypot input catches naive bots.
    """

    template_name = "formbuilder/public_form.html"

    def get_send(self):
        return get_object_or_404(FormSend.objects.select_related("form").filter(club=self.request.club, is_public=True), public_token=self.kwargs["token"])

    def get_member(self, request):
        # Same idea as registration.views.RegistrationView.get_contact_member --
        # a visitor who happens to be logged in still gets linked to their own
        # Member record rather than treated as a stranger.
        if not request.user.is_authenticated:
            return None
        return Member.objects.filter(user=request.user).first()

    def get(self, request, *args, **kwargs):
        send = self.get_send()
        return render(request, self.template_name, {"send": send, "form": build_form(send.form), "honeypot_field": HONEYPOT_FIELD})

    def post(self, request, *args, **kwargs):
        send = self.get_send()

        # Honeypot: respond exactly as a successful submission would, without
        # validating or saving anything (same as registration.views.RegistrationView).
        if request.POST.get(HONEYPOT_FIELD):
            return self.submitted(request, send)

        # Per send *and* per IP, so one busy link can't lock a visitor out of
        # another. Checked up front, counted only once a response is saved.
        throttle_key = f"{send.pk}:{client_ip(request) or 'unknown'}"
        if throttle.is_limited(PUBLIC_FORM_IP_RATE, throttle_key):
            bound_form = build_form(send.form, data=request.POST, files=request.FILES)
            bound_form.add_error(None, _("Too many responses have been submitted from this connection recently. Please try again later."))
            return render(request, self.template_name, {"send": send, "form": bound_form, "honeypot_field": HONEYPOT_FIELD}, status=429)

        try:
            submit_form(send, self.get_member(request), request.POST, files=request.FILES)
        except FormSubmissionError as error:
            # Same is_valid()-first idiom as mobile.views.FormFillView.post --
            # see that view's own comment for why (avoids double-reporting a
            # field error already raised once inside submit_form).
            bound_form = build_form(send.form, data=request.POST, files=request.FILES)
            bound_form.is_valid()
            if not error.errors:
                bound_form.add_error(None, str(error))
            notify(request, f"e|{_('Could not submit')}|{error}")
            return render(request, self.template_name, {"send": send, "form": bound_form, "honeypot_field": HONEYPOT_FIELD})

        throttle.record(PUBLIC_FORM_IP_RATE, throttle_key)
        return self.submitted(request, send)

    def submitted(self, request, send):
        notify(request, f"s|{_('Submitted')}|{_('Thanks -- your response has been recorded.')}")
        return redirect("formbuilder:fill", token=send.public_token)


def can_download_answer_file(request, answer) -> bool:
    """Whoever may see this response: the club's form managers -- the same
    gate as the management Responses page (club.mixins.FormManagerRequiredMixin:
    the "formbuilder" feature on for this club, plus can_manage_forms) -- or
    the signed-in person who filled it in, or the member it was filled in for."""
    user = request.user
    submission = answer.submission
    if flag_is_active(request, "formbuilder") and can_manage_forms(user, request.club):
        return True
    own_members = {member for member in (submission.submitted_by, submission.member) if member is not None}
    return any(member.user_id == user.pk for member in own_members)


class AnswerFileView(View):
    """Streams a FILE answer's upload from private storage. Club-scoped (the
    answer must belong to a send of this host's club), and only for
    can_download_answer_file; anyone else gets a 404 that doesn't confirm the
    answer exists. Always an attachment, never rendered inline."""

    def get(self, request, pk):
        if getattr(request, "club", None) is None:
            raise Http404("No club on this request.")
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        answer = get_object_or_404(Answer.objects.select_related("submission__member", "submission__submitted_by").filter(submission__send__club=request.club), pk=pk)
        if not answer.file or not can_download_answer_file(request, answer):
            raise Http404("No such file.")
        try:
            handle = answer.file.open("rb")
        except FileNotFoundError as error:
            raise Http404("No such file.") from error

        response = FileResponse(handle, as_attachment=True, filename=answer.file_name, content_type="application/octet-stream")
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        patch_vary_headers(response, ["Cookie"])
        return response
