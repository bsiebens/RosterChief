from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from .club_switch import club_picker_rows
from .mixins import MemberAppMixin


class ClubPickerView(MemberAppMixin, TemplateView):
    """ "Choose Your Club" -- every club the account is tied to (iOS ClubPicker). Gated like
    the rest of the member app, so it only opens on a club the account belongs to."""

    template_name = "mobile/club_picker.html"
    screen_title = _("Choose Your Club")
    active_tab = "me"

    def get_context_data(self, **kwargs):
        return super().get_context_data(rows=club_picker_rows(self.request), screen_title=self.screen_title, **kwargs)
