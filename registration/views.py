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

from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View

from club.models import MemberRequirementStatus, OnboardingRequirement
from club.services.cancellation import cancel_membership
from club.services.fees import net_balance
from club.services.onboarding import checklist_for, mark_complete
from controlpanel.messages import notify
from members.models import Member
from members.views import ClubScopedPublicMixin
from rosterchief import throttle
from rosterchief.client_ip import client_ip

from .forms import RegistrationContactForm, RegistrationEntryFormSet, RegistrationStatusDocumentForm, entries_from_formset
from .models import RegistrationBatch, RegistrationDetails
from .services import PricingError, RegistrationError, price_entries, priced_rows_with_jersey_fields, resolve_chosen_season, resolve_registration_season, submit_registration, team_number_pools, variant_registration_kinds
from .services.invoicing import RegistrationInvoicePDFError, active_batch_entries, batch_early_payment_offer, batch_invoice_pdf, batch_totals
from .services.notifications import send_registration_confirmation_email

#: Caps on final "Confirm & submit" POSTs (price calculations aren't counted):
#: per client IP, and per contact email address (case-insensitive).
REGISTRATION_IP_RATE = throttle.Rate("registration:ip", limit=5, window=60 * 60)
REGISTRATION_EMAIL_RATE = throttle.Rate("registration:email", limit=3, window=24 * 60 * 60)


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
        entry_formset = RegistrationEntryFormSet(data, club=request.club, season=season, team_number_pools=team_number_pools(request.club, season), prefix="entries")
        return contact_form, entry_formset

    def render_season_picker(self, request, available_seasons):
        return render(request, self.template_name, {"registration_open": bool(available_seasons), "needs_season_choice": True, "available_seasons": available_seasons})

    def render_page(self, request, season, contact_form, entry_formset, priced_entries=None, status=200):
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
                "priced_total": sum((row["price"] - row["min_registrants_discount"] for _form, _entry, row in priced_entries), Decimal("0")) if priced_entries else None,
                # What the same total comes to if every entry with an early-
                # payment deadline (Product.early_bird_discount_*) is paid by
                # its own date -- 0 when nothing in the batch has one, so the
                # template only shows this line (priced_total != priced_early_total)
                # when it's actually worth showing.
                "priced_early_total": sum((row["price"] - row["min_registrants_discount"] - row["deadline_discount"] for _form, _entry, row in priced_entries), Decimal("0")) if priced_entries else None,
                "registration_open": True,
                "season": season,
                "locked_member": self.get_contact_member(request),
                "variant_registration_kinds": variant_registration_kinds(request.club, season),
                "team_number_pools": team_number_pools(request.club, season),
            },
            status=status,
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

        # Honeypot (RegistrationContactForm.website): respond exactly as a
        # successful submission would, without validating or saving anything.
        if request.POST.get("action") == "submit" and request.POST.get("website"):
            self.notify_received(request)
            return redirect("registration:register")

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

        priced = price_entries([entry.product_variant for entry in entries])
        priced_rows = priced_rows_with_jersey_fields(entry_formset, entries, priced, team_number_pools(request.club, season))

        if request.POST.get("action") != "submit":
            return self.render_page(request, season, contact_form, entry_formset, priced_entries=priced_rows)

        member = self.get_contact_member(request)
        contact = dict(contact_form.cleaned_data)
        contact.pop("website", None)
        if member is not None:
            contact = {"contact_first_name": member.first_name, "contact_last_name": member.last_name, "contact_email": member.contact_email, "contact_phone": contact.get("contact_phone", "")}

        if throttle.check_and_record((REGISTRATION_IP_RATE, client_ip(request) or "unknown"), (REGISTRATION_EMAIL_RATE, (contact.get("contact_email") or "").strip().lower())):
            contact_form.add_error(None, _("Too many registrations have been submitted recently. Please try again later, or contact the club directly."))
            return self.render_page(request, season, contact_form, entry_formset, priced_entries=priced_rows, status=429)

        try:
            batch = submit_registration(request.club, submitted_by_user=request.user if request.user.is_authenticated else None, entries=entries, **contact)
        except RegistrationError as error:
            contact_form.add_error(None, str(error))
            return self.render_page(request, season, contact_form, entry_formset)

        send_registration_confirmation_email(batch, request=request)
        self.notify_received(request)
        return redirect("registration:status", token=batch.status_token)

    def notify_received(self, request):
        notify(request, f"s|{_('Registration received')}|{_('Thanks -- the club will review this and be in touch. A confirmation email is on its way with a link to check status.')}")


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
    actually activate a membership, so a self-upload alone can't skip that.

    Onboarding (the above) is unconditional -- billing isn't: balance/
    early-payment/"how to pay"/invoice-download only show once staff has
    reviewed and confirmed this registration's own invoice on the management
    Registrations screen (RegistrationBatch.invoice_sent_at), see
    registration.services.invoicing's own module docstring. Nothing
    financial reaches a family before that, however long onboarding takes."""

    template_name = "registration/status.html"

    def get_batch(self, request, token):
        return get_object_or_404(RegistrationBatch.objects.select_related("season"), club=request.club, status_token=token)

    def get_membership_rows(self, batch, invoice_ready):
        memberships = list({details.membership for details in RegistrationDetails.objects.filter(batch=batch).select_related("membership__member")})
        memberships.sort(key=lambda membership: membership.member.get_full_name())
        rows = []
        for membership in memberships:
            managed_here = membership.created >= batch.created  # see RegistrationDetails.created_membership
            # net_balance, not remaining_balance -- the latter floors at 0,
            # which would fold a genuine credit (a negative per-line price on
            # the Registrations review screen, or an overpayment) into the
            # same "No balance owed" reading as someone who simply owes
            # nothing, hiding a real number status.html's own template needs
            # to show instead. ``credit`` is that same figure flipped
            # positive, for the template to print without doing arithmetic
            # of its own.
            balance = net_balance(membership) if invoice_ready else None
            rows.append(
                {
                    "membership": membership,
                    "balance": balance,
                    "credit": -balance if balance is not None and balance < 0 else None,
                    # Once anything's been paid (or nothing's still owed on a
                    # sent invoice), "Cancel registration" is no longer offered
                    # -- the same cancellation_locked() check
                    # RegistrationCancelView enforces server-side.
                    "paid": cancellation_locked(membership, invoice_ready),
                    # Cancel/upload only for a membership this batch itself
                    # created -- one that already existed isn't this link's
                    # to change (see RegistrationDetails.created_membership).
                    "managed_here": managed_here,
                    "checklist": checklist_for(membership),
                    "upload_form": RegistrationStatusDocumentForm(),
                }
            )
        return rows

    def get(self, request, *args, **kwargs):
        batch = self.get_batch(request, kwargs["token"])
        invoice_ready = batch.invoice_sent_at is not None
        # One combined "pay early and save" figure for the whole registration,
        # not one per person -- computed against the batch's own total (net
        # of both the multi-registrant and any manual discount), so it can
        # never silently ignore either. See registration.services.invoicing.
        # batch_early_payment_offer's own docstring.
        early_payment = None
        if invoice_ready:
            entries = active_batch_entries(batch)
            _subtotal, _discount_amount, total = batch_totals(entries, batch.manual_discount_amount)
            early_payment = batch_early_payment_offer(batch, entries, total)
        return render(
            request,
            self.template_name,
            {
                "batch": batch,
                "membership_rows": self.get_membership_rows(batch, invoice_ready),
                "payment_instructions": batch.club.payment_instructions,
                "invoice_ready": invoice_ready,
                "early_payment": early_payment,
            },
        )

    def post(self, request, *args, **kwargs):
        batch = self.get_batch(request, kwargs["token"])
        membership_id = request.POST.get("membership")
        requirement_id = request.POST.get("requirement")
        # Both must belong to this batch/club -- a tampered POST can't reach
        # another family's membership or another club's requirement through
        # this token.
        details = RegistrationDetails.objects.filter(batch=batch, membership_id=membership_id).select_related("membership", "batch").first()
        requirement = OnboardingRequirement.objects.filter(club=request.club, pk=requirement_id, is_active=True, requires_document=True).first()
        if details is None or requirement is None or not details.created_membership:
            raise Http404
        # Only an open item can be uploaded against -- never over something
        # already received/verified or bypassed (the template hides the form
        # for those too; this is the server-side half of that).
        if MemberRequirementStatus.objects.filter(membership=details.membership, requirement=requirement).filter(Q(is_complete=True) | Q(is_bypassed=True)).exists():
            notify(request, f"e|{_('Upload failed')}|{_('This item has already been completed.')}")
            return redirect("registration:status", token=batch.status_token)

        form = RegistrationStatusDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            mark_complete(details.membership, requirement, user=None, document=form.cleaned_data["document"], note=_("Uploaded by the registrant via the status page."))
            notify(request, f"s|{_('Document received')}|{_('“%(requirement)s” has been received.') % {'requirement': requirement.name}}")
        else:
            errors = form.errors.get("document") or [_("Choose a file to upload.")]
            notify(request, f"e|{_('Upload failed')}|{errors[0]}")

        return redirect("registration:status", token=batch.status_token)


class RegistrationInvoiceView(ClubScopedPublicMixin, View):
    """One PDF covering every entry in the batch (registration.services.
    invoicing.batch_invoice_pdf), not one per membership -- reached from the
    status page the same token-gated way as everything else there. 404s
    until staff has confirmed the invoice (RegistrationBatch.invoice_sent_at)
    -- there's no document to hand out before then, see RegistrationStatusView's
    own docstring."""

    def get(self, request, *args, **kwargs):
        batch = get_object_or_404(RegistrationBatch, club=request.club, status_token=kwargs["token"])
        if batch.invoice_sent_at is None:
            raise Http404("This registration's invoice hasn't been confirmed yet.")

        try:
            pdf = batch_invoice_pdf(batch)
        except RegistrationInvoicePDFError as error:
            notify(request, f"e|{_('PDF unavailable')}|{error}")
            return redirect("registration:status", token=batch.status_token)

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{batch.club.slug}-registration-{batch.pk}.pdf"'
        return response


def cancellation_locked(membership, invoice_ready):
    """Whether the status page's own "Cancel registration" is off the table
    for ``membership``: once any payment has been recorded against it, or
    once its invoice has gone out and nothing is left owed (the balance is
    settled). Undoing a paid registration means a refund, which is a staff
    action on the management side, not something a status link can trigger.
    Shared by the status page (to hide the button) and RegistrationCancelView
    (to refuse the POST), so the two can't disagree."""
    if membership.amount_paid > 0:
        return True
    return invoice_ready and net_balance(membership) <= 0


class RegistrationCancelView(ClubScopedPublicMixin, View):
    """Withdraws one person's registration from the status page -- same
    token-gated access as everything else there, and the same club.services.
    cancellation.cancel_membership every cancel path (this one, and the
    Sign-up page's own) goes through -- which never deletes a Member with a
    login or with anything outside this sign-up. Limited to memberships this
    batch itself created (RegistrationDetails.created_membership), and refused
    once anything has been paid toward it (cancellation_locked) -- the
    status page hides the button then, and this enforces the same rule for
    a POST that doesn't go through that page."""

    def post(self, request, *args, **kwargs):
        batch = get_object_or_404(RegistrationBatch, club=request.club, status_token=kwargs["token"])
        details = RegistrationDetails.objects.filter(batch=batch, membership_id=kwargs["membership_pk"]).select_related("membership__member", "batch").first()
        # A membership that predates this batch (the batch only added a
        # request onto it) isn't this link's to cancel -- same 404 as one
        # outside the batch entirely.
        if details is None or not details.created_membership:
            raise Http404

        member_name = str(details.membership.member)
        if cancellation_locked(details.membership, batch.invoice_sent_at is not None):
            title = _("Registration not cancelled")
            body = _("A payment has already been made toward the registration for “%(member)s”, so it can't be cancelled here. Please contact the club.") % {"member": member_name}
            notify(request, f"e|{title}|{body}")
            return redirect("registration:status", token=batch.status_token)

        cancel_membership(details.membership)

        notify(request, f"w|{_('Registration cancelled')}|{_('The registration for “%(member)s” has been cancelled.') % {'member': member_name}}")
        return redirect("registration:status", token=batch.status_token)
