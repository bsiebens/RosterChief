from django.urls import path

from . import views

app_name = "management"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    # People
    path("members/", views.MemberListView.as_view(), name="member_list"),
    path("memberships/", views.MembershipListView.as_view(), name="membership_list"),
    path("memberships/mark-paid/", views.MembershipMarkPaidView.as_view(), name="membership_mark_paid"),
    path("memberships/export/", views.MembershipExportPdfView.as_view(), name="membership_export_pdf"),
    path("memberships/<uuid:pk>/mark-fully-paid/", views.MembershipMarkFullyPaidView.as_view(), name="membership_mark_fully_paid"),
    path("memberships/<uuid:pk>/record-payment/", views.MembershipRecordPaymentView.as_view(), name="membership_record_payment"),
    path("members/new/", views.MemberCreateView.as_view(), name="member_create"),
    path("members/import/template/", views.MemberImportTemplateView.as_view(), name="member_import_template"),
    path("members/import/", views.MemberImportView.as_view(), name="member_import"),
    path("members/import/confirm/", views.MemberImportConfirmView.as_view(), name="member_import_confirm"),
    path("members/<uuid:pk>/", views.MemberDetailView.as_view(), name="member_detail"),
    path("members/<uuid:pk>/edit/", views.MemberUpdateView.as_view(), name="member_update"),
    path("members/<uuid:pk>/delete/", views.MemberDeleteView.as_view(), name="member_delete"),
    path("members/<uuid:pk>/attach-family/", views.MemberAttachToFamilyView.as_view(), name="member_attach_family"),
    path("members/<uuid:pk>/grant-login/", views.MemberGrantLoginView.as_view(), name="member_grant_login"),
    path("members/<uuid:pk>/detach-family/<uuid:family_pk>/", views.MemberDetachFromFamilyView.as_view(), name="member_detach_family"),
    path("families/new/", views.FamilyCreateView.as_view(), name="family_create"),
    path("families/<uuid:pk>/", views.FamilyDetailView.as_view(), name="family_detail"),
    path("families/<uuid:pk>/add-child/", views.FamilyAddChildView.as_view(), name="family_add_child"),
    path("families/<uuid:pk>/add-parent/", views.FamilyAddParentView.as_view(), name="family_add_parent"),
    path("families/<uuid:family_pk>/members/<uuid:member_pk>/role/", views.FamilyMembershipRoleUpdateView.as_view(), name="family_membership_role_update"),
    # Club setup (admin only)
    path("positions/", views.PositionListView.as_view(), name="position_list"),
    path("positions/new/", views.PositionCreateView.as_view(), name="position_create"),
    path("positions/<uuid:pk>/edit/", views.PositionUpdateView.as_view(), name="position_update"),
    path("roles/", views.ClubRoleListView.as_view(), name="role_list"),
    path("roles/new/", views.ClubRoleCreateView.as_view(), name="role_create"),
    path("roles/<uuid:pk>/revoke/", views.ClubRoleRevokeView.as_view(), name="role_revoke"),
    # Teams
    path("teams/", views.TeamListView.as_view(), name="team_list"),
    path("teams/new/", views.TeamCreateView.as_view(), name="team_create"),
    path("teams/<uuid:pk>/", views.TeamDetailView.as_view(), name="team_detail"),
    path("teams/<uuid:pk>/edit/", views.TeamUpdateView.as_view(), name="team_update"),
    path("teams/<uuid:pk>/delete/", views.TeamDeleteView.as_view(), name="team_delete"),
    path("teams/<uuid:pk>/roster/<uuid:season_pk>/add/", views.TeamRosterAddView.as_view(), name="team_roster_add"),
    path("teams/<uuid:pk>/roster/<uuid:membership_pk>/edit/", views.TeamRosterUpdateView.as_view(), name="team_roster_update"),
    path("teams/<uuid:pk>/roster/<uuid:membership_pk>/remove/", views.TeamRosterRemoveView.as_view(), name="team_roster_remove"),
    path("teams/<uuid:pk>/staff/<uuid:season_pk>/add/", views.TeamStaffAddView.as_view(), name="team_staff_add"),
    path("teams/<uuid:pk>/staff/<uuid:assignment_pk>/edit/", views.TeamStaffUpdateView.as_view(), name="team_staff_update"),
    path("teams/<uuid:pk>/staff/<uuid:assignment_pk>/remove/", views.TeamStaffRemoveView.as_view(), name="team_staff_remove"),
    # News
    path("news/", views.NewsListView.as_view(), name="news_list"),
    path("news/new/", views.NewsCreateView.as_view(), name="news_create"),
    path("news/<uuid:pk>/", views.NewsDetailView.as_view(), name="news_detail"),
    path("news/<uuid:pk>/edit/", views.NewsUpdateView.as_view(), name="news_update"),
    path("news/<uuid:pk>/delete/", views.NewsDeleteView.as_view(), name="news_delete"),
    path("news/<uuid:pk>/publish/", views.NewsPublishView.as_view(), name="news_publish"),
    path("news/<uuid:pk>/unpublish/", views.NewsUnpublishView.as_view(), name="news_unpublish"),
    path("news/<uuid:pk>/photos/", views.NewsPhotoUploadView.as_view(), name="news_photo_upload"),
    path("news/<uuid:pk>/photos/<uuid:photo_pk>/set-main/", views.NewsPhotoSetMainView.as_view(), name="news_photo_set_main"),
    path("news/<uuid:pk>/photos/<uuid:photo_pk>/delete/", views.NewsPhotoDeleteView.as_view(), name="news_photo_delete"),
    # Calendar
    path("events/", views.EventListView.as_view(), name="event_list"),
    path("event-series/", views.EventSeriesListView.as_view(), name="event_series_list"),
    path("locations/", views.LocationListView.as_view(), name="location_list"),
    path("opponents/", views.OpponentListView.as_view(), name="opponent_list"),
    # Shop (admin only)
    path("shop/products/", views.ProductListView.as_view(), name="product_list"),
    path("shop/orders/", views.OrderListView.as_view(), name="order_list"),
    path("shop/discounts/", views.DiscountListView.as_view(), name="discount_list"),
    path("shop/invoices/", views.InvoiceListView.as_view(), name="invoice_list"),
    # Forms
    path("forms/", views.FormListView.as_view(), name="form_list"),
    path("forms/<uuid:pk>/submissions/", views.SubmissionListView.as_view(), name="submission_list"),
]
