from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View

from controlpanel.messages import notify
from members.models import Member
from members.views import ClubScopedPublicMixin

from .models import FormSend
from .services.form_factory import build_form
from .services.submission import FormSubmissionError, submit_form


class PublicFormFillView(ClubScopedPublicMixin, View):
    """The public, unauthenticated form-fill page -- linkable from wherever a
    club shares it (their own site, email, social...), same "public, club-
    scoped, no login" shape as registration.views.RegistrationView. Reached
    by FormSend.public_token, only ever looked up with is_public=True (see
    get_send) -- opting a send into having a link at all is a deliberate,
    separate choice from whether an anonymous submission is actually let
    through, which is still entirely formbuilder.services.submission.
    submit_form's own call: Form.login_required gates that, not this view.
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
        return render(request, self.template_name, {"send": send, "form": build_form(send.form)})

    def post(self, request, *args, **kwargs):
        send = self.get_send()
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
            return render(request, self.template_name, {"send": send, "form": bound_form})

        notify(request, f"s|{_('Submitted')}|{_('Thanks -- your response has been recorded.')}")
        return redirect("formbuilder:fill", token=send.public_token)
