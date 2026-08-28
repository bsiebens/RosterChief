"""The public, unauthenticated registration page -- linkable from a club's
own website. Two server round-trips, no client-side pricing logic (matching
this codebase's existing conventions): the first POST ("Calculate price")
prices the batch and shows it back without saving anything; the second
("Confirm & submit", a hidden field on the same form) actually calls
registration.services.submission.submit_registration. See that module's own
docstring for what happens from there -- straight into the existing Sign-up
queue, no separate review gate of this app's own.
"""

from decimal import Decimal

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View

from club.models import OnboardingRequirement
from club.services.fees import remaining_balance
from club.services.onboarding import checklist_for, mark_complete
from controlpanel.messages import notify
from members.models import Member
from members.views import ClubScopedPublicMixin

from .forms import RegistrationContactForm, RegistrationEntryFormSet, RegistrationStatusDocumentForm, entries_from_formset
from .models import RegistrationBatch, RegistrationDetails
from .services import PricingError, RegistrationError, price_entries, resolve_chosen_season, resolve_registration_season, submit_registration, variant_registration_kinds
from .services.invoicing import RegistrationInvoicePDFError, batch_invoice_pdf
from .services.notifications import send_registration_confirmation_email


class RegistrationView(ClubScopedPublicMixin, View):
    """A season is resolved before anything else renders (get_season below)
    -- almost always automatic (there's only ever one open registration
    window most of the time), but when two are open at once (a late
    sign-up for the outgoing season alongside the new one already open)
    the registrant is shown a season picker instead of the entry form, and
    every subsequent GET/POST carries the chosen one forward via a
    ``season`` field so a batch can never mix products from two different
    seasons."""

    template_name = "registration/register.html"

    def get_contact_member(self, request):
        if not request.user.is_authenticated:
            return None
        return Member.objects.filter(user=request.user).first()

    def get_season(self, request, data=None):
        source = data if data is not None else request.GET
        return resolve_chosen_season(request.club, source.get("season"))

    def get_forms(self, request, season, data=None):
        member = self.get_contact_member(request)
        contact_form = RegistrationContactForm(data, lock_contact_fields=member is not None)
        entry_formset = RegistrationEntryFormSet(data, club=request.club, season=season, prefix="entries")
        return contact_form, entry_formset

    def render_season_picker(self, request, available_seasons):
        return render(request, self.template_name, {"registration_open": bool(available_seasons), "needs_season_choice": True, "available_seasons": available_seasons})

    def render_page(self, request, season, contact_form, entry_formset, priced_entries=None):
        return render(
            request,
            self.template_name,
            {
                "form": contact_form,
                "entry_formset": entry_formset,
                "priced_entries": priced_entries,
                # Same per-entry amount submit_registration actually charges
                # (price minus its own min-registrants discount -- the
                # early-payment one is conditional, never baked into what's
                # shown as owed now, see registration.services.pricing's own
                # module docstring), summed once so the receipt can show a
                # total instead of only itemised lines.
                "priced_total": sum((row["price"] - row["min_registrants_discount"] for _entry, row in priced_entries), Decimal("0")) if priced_entries else None,
                # What the same total comes to if every entry with an early-
                # payment deadline (Product.early_bird_discount_*) is paid by
                # its own date -- 0 when nothing in the batch has one, so the
                # template only shows this line (priced_total != priced_early_total)
                # when it's actually worth showing.
                "priced_early_total": sum((row["price"] - row["min_registrants_discount"] - row["deadline_discount"] for _entry, row in priced_entries), Decimal("0")) if priced_entries else None,
                "registration_open": True,
                "season": season,
                "locked_member": self.get_contact_member(request),
                "variant_registration_kinds": variant_registration_kinds(request.club, season),
            },
        )

    def get(self, request, *args, **kwargs):
        season, available_seasons = self.get_season(request)
        if season is None:
            return self.render_season_picker(request, available_seasons)

        contact_form, entry_formset = self.get_forms(request, season)
        return self.render_page(request, season, contact_form, entry_formset)

    def post(self, request, *args, **kwargs):
        season, available_seasons = self.get_season(request, data=request.POST)
        if season is None:
            return self.render_season_picker(request, available_seasons)

        contact_form, entry_formset = self.get_forms(request, season, request.POST)
        if not contact_form.is_valid() or not entry_formset.is_valid():
            return self.render_page(request, season, contact_form, entry_formset)

        entries = entries_from_formset(entry_formset)
        try:
            # Validated here too (not just inside submit_registration) --
            # belt and braces alongside the entry formset's own season-
            # scoped product_variant queryset, so a tampered POST can't
            # sneak a variant from a different season through.
            resolve_registration_season([entry.product_variant for entry in entries])
        except PricingError as error:
            contact_form.add_error(None, str(error))
            return self.render_page(request, season, contact_form, entry_formset)

        priced_rows = list(zip(entries, price_entries([entry.product_variant for entry in entries]), strict=True))

        if request.POST.get("action") != "submit":
            return self.render_page(request, season, contact_form, entry_formset, priced_entries=priced_rows)

        member = self.get_contact_member(request)
        contact = dict(contact_form.cleaned_data)
        if member is not None:
            contact = {"contact_first_name": member.first_name, "contact_last_name": member.last_name, "contact_email": member.contact_email, "contact_phone": contact.get("contact_phone", "")}

        try:
            batch = submit_registration(request.club, submitted_by_user=request.user if request.user.is_authenticated else None, entries=entries, **contact)
        except RegistrationError as error:
            contact_form.add_error(None, str(error))
            return self.render_page(request, season, contact_form, entry_formset)

        send_registration_confirmation_email(batch, request=request)
        notify(request, f"s|{_('Registration received')}|{_('Thanks -- the club will review this and be in touch. A confirmation email is on its way with a link to check status.')}")
        return redirect("registration:status", token=batch.status_token)


class RegistrationStatusView(ClubScopedPublicMixin, View):
    """The link registration.services.notifications.send_registration_confirmation_email
    hands out -- unguessable-token access (RegistrationBatch.status_token,
    same idea as mobile.models.CalendarFeedToken) rather than a login, since
    whoever submitted the batch may have no account at all.

    Shows where every entry in the batch stands (ClubMembership.status, fee
    balance) and its onboarding checklist (club.services.onboarding.
    checklist_for), with an upload form for any open, document-requiring
    requirement -- feeding straight into the same checklist staff already
    works from (management's own Documents tab), rather than a parallel
    inbox somewhere. ``user=None`` on mark_complete below (its ``completed_by``
    is nullable) is how staff can tell an item was self-uploaded rather than
    verified by them -- still needs the deliberate Sign-up "Approve" step
    club.services.onboarding.approve_one/approve_all_clean gate on to
    actually activate a membership, so a self-upload alone can't skip that."""

    template_name = "registration/status.html"

    def get_batch(self, request, token):
        return get_object_or_404(RegistrationBatch.objects.select_related("season"), club=request.club, status_token=token)

    def get_membership_rows(self, batch):
        memberships = list({details.membership for details in RegistrationDetails.objects.filter(batch=batch).select_related("membership__member")})
        memberships.sort(key=lambda membership: membership.member.get_full_name())
        rows = []
        for membership in memberships:
            rows.append(
                {
                    "membership": membership,
                    "balance": remaining_balance(membership),
                    "checklist": checklist_for(membership),
                    "upload_form": RegistrationStatusDocumentForm(),
                }
            )
        return rows

    def get(self, request, *args, **kwargs):
        batch = self.get_batch(request, kwargs["token"])
        return render(request, self.template_name, {"batch": batch, "membership_rows": self.get_membership_rows(batch)})

    def post(self, request, *args, **kwargs):
        batch = self.get_batch(request, kwargs["token"])
        membership_id = request.POST.get("membership")
        requirement_id = request.POST.get("requirement")
        # Both must belong to this batch/club -- a tampered POST can't reach
        # another family's membership or another club's requirement through
        # this token.
        details = RegistrationDetails.objects.filter(batch=batch, membership_id=membership_id).select_related("membership").first()
        requirement = OnboardingRequirement.objects.filter(club=request.club, pk=requirement_id, is_active=True, requires_document=True).first()
        if details is None or requirement is None:
            raise Http404

        form = RegistrationStatusDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            mark_complete(details.membership, requirement, user=None, document=form.cleaned_data["document"], note=_("Uploaded by the registrant via the status page."))
            notify(request, f"s|{_('Document received')}|{_('“%(requirement)s” has been received.') % {'requirement': requirement.name}}")
        else:
            notify(request, f"e|{_('Upload failed')}|{_('Choose a file to upload.')}")

        return redirect("registration:status", token=batch.status_token)


class RegistrationInvoiceView(ClubScopedPublicMixin, View):
    """One PDF covering every entry in the batch (registration.services.
    invoicing.batch_invoice_pdf), not one per membership -- reached from the
    status page the same token-gated way as everything else there."""

    def get(self, request, *args, **kwargs):
        batch = get_object_or_404(RegistrationBatch, club=request.club, status_token=kwargs["token"])

        try:
            pdf = batch_invoice_pdf(batch)
        except RegistrationInvoicePDFError as error:
            notify(request, f"e|{_('PDF unavailable')}|{error}")
            return redirect("registration:status", token=batch.status_token)

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{batch.club.slug}-registration-{batch.pk}.pdf"'
        return response
