"""The public marketing homepage -- rosterchief.app's bare domain (``request.club
is None``). Reached only via club.views.root()'s delegation, not its own URL
pattern: the page is a single scrolling document with anchor navigation (see
design_handoff_rosterchief_platform/WEBSITE.md), and its contact form posts
straight back to "/" rather than to a separate endpoint.
"""

from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from rosterchief.mail import send_message

from .forms import ContactForm, fresh_timestamp_token
from .models import ContactRequest

#: WEBSITE.md item 6: only these three CTAs carry plan context on their link;
#: anything else is an unqualified "?plan=" and is dropped rather than trusted
#: as free text into the notification email/record.
KNOWN_PLANS = {"club", "large", "website"}


def home(request):
    contact_message = None

    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            _save_and_notify(request, form)
            messages.success(request, _("We've sent your message straight to Bernard — you'll hear back within two working days."), extra_tags="contact_sent")
            return redirect(reverse("root") + "#contact")
        status = 400
    else:
        plan = request.GET.get("plan", "")
        form = ContactForm(initial={"plan": plan if plan in KNOWN_PLANS else "", "ts": fresh_timestamp_token()})
        # Consumed here rather than left to the template's own `messages` context
        # processor, since only this one flash (not any other message the session
        # happens to carry) should ever swap the contact card for the success state.
        contact_message = next((str(m.message) for m in messages.get_messages(request) if "contact_sent" in m.tags), None)
        status = 200

    context = {"form": form, "contact_message": contact_message, "contact_email": settings.ROSTERCHIEF_CONTACT_EMAIL}
    return render(request, "marketing/home.html", context, status=status)


def _save_and_notify(request, form: ContactForm) -> None:
    data = form.cleaned_data
    contact = ContactRequest.objects.create(
        name=data["name"],
        email=data["email"],
        club=data["club"],
        members=data["members"],
        role=data["role"],
        message=data["message"],
        plan_interest=data["plan"],
        ip=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:300],
    )

    subject = _("RosterChief demo request — %(club)s") % {"club": contact.club}
    body = render_to_string("marketing/email/contact_notification.txt", {"contact": contact})
    message = EmailMultiAlternatives(subject=str(subject), body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=[settings.ROSTERCHIEF_CONTACT_EMAIL], reply_to=[contact.email])
    send_message(message, fail_silently=False)
