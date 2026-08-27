"""The public, unauthenticated registration page -- linkable from a club's
own website. Two server round-trips, no client-side pricing logic (matching
this codebase's existing conventions): the first POST ("Calculate price")
prices the batch and shows it back without saving anything; the second
("Confirm & submit", a hidden field on the same form) actually calls
registration.services.submission.submit_registration. See that module's own
docstring for what happens from there -- straight into the existing Sign-up
queue, no separate review gate of this app's own.
"""

from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View

from controlpanel.messages import notify
from members.models import Member
from members.views import ClubScopedPublicMixin

from .forms import RegistrationContactForm, RegistrationEntryFormSet, entries_from_formset
from .services import PricingError, RegistrationError, available_registration_products, price_entries, resolve_registration_season, submit_registration


class RegistrationView(ClubScopedPublicMixin, View):
    template_name = "registration/register.html"

    def get_contact_member(self, request):
        if not request.user.is_authenticated:
            return None
        return Member.objects.filter(user=request.user).first()

    def get_forms(self, request, data=None):
        member = self.get_contact_member(request)
        contact_form = RegistrationContactForm(data, lock_contact_fields=member is not None)
        entry_formset = RegistrationEntryFormSet(data, club=request.club, prefix="entries")
        return contact_form, entry_formset

    def render_page(self, request, contact_form, entry_formset, priced_entries=None):
        registration_open = available_registration_products(request.club).exists()
        return render(
            request,
            self.template_name,
            {
                "form": contact_form,
                "entry_formset": entry_formset,
                "priced_entries": priced_entries,
                "registration_open": registration_open,
                "locked_member": self.get_contact_member(request),
            },
        )

    def get(self, request, *args, **kwargs):
        contact_form, entry_formset = self.get_forms(request)
        return self.render_page(request, contact_form, entry_formset)

    def post(self, request, *args, **kwargs):
        contact_form, entry_formset = self.get_forms(request, request.POST)
        if not contact_form.is_valid() or not entry_formset.is_valid():
            return self.render_page(request, contact_form, entry_formset)

        entries = entries_from_formset(entry_formset)
        try:
            # Validated here too (not just inside submit_registration) so a
            # mismatched-season error already shows up at the preview step,
            # not only once someone tries to actually submit.
            resolve_registration_season([entry.product_variant for entry in entries])
        except PricingError as error:
            contact_form.add_error(None, str(error))
            return self.render_page(request, contact_form, entry_formset)

        priced_rows = list(zip(entries, price_entries([entry.product_variant for entry in entries]), strict=True))

        if request.POST.get("action") != "submit":
            return self.render_page(request, contact_form, entry_formset, priced_entries=priced_rows)

        member = self.get_contact_member(request)
        contact = dict(contact_form.cleaned_data)
        if member is not None:
            contact = {"contact_first_name": member.first_name, "contact_last_name": member.last_name, "contact_email": member.contact_email, "contact_phone": contact.get("contact_phone", "")}

        try:
            submit_registration(request.club, submitted_by_user=request.user if request.user.is_authenticated else None, entries=entries, **contact)
        except RegistrationError as error:
            contact_form.add_error(None, str(error))
            return self.render_page(request, contact_form, entry_formset)

        notify(request, f"s|{_('Registration received')}|{_('Thanks -- the club will review this and be in touch.')}")
        return redirect("registration:register")
