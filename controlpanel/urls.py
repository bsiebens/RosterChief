from django.urls import path

from . import views

app_name = "controlpanel"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    # Clubs
    path("clubs/", views.ClubListView.as_view(), name="club_list"),
    path("clubs/new/", views.ClubCreateView.as_view(), name="club_create"),
    path("clubs/<uuid:pk>/", views.ClubDetailView.as_view(), name="club_detail"),
    path("clubs/<uuid:pk>/edit/", views.ClubUpdateView.as_view(), name="club_update"),
    path("clubs/<uuid:pk>/archive/", views.ClubArchiveView.as_view(), name="club_archive"),
    path("clubs/<uuid:pk>/restore/", views.ClubRestoreView.as_view(), name="club_restore"),
    path("clubs/<uuid:pk>/home-location/", views.ClubHomeLocationSetView.as_view(), name="club_home_location_set"),
    path("clubs/<uuid:pk>/admins/add/", views.ClubAdminAddView.as_view(), name="club_admin_add"),
    path("clubs/<uuid:pk>/admins/<uuid:role_pk>/remove/", views.ClubAdminRemoveView.as_view(), name="club_admin_remove"),
    path("clubs/<uuid:pk>/features/<int:flag_pk>/toggle/", views.ClubFeatureToggleView.as_view(), name="club_feature_toggle"),
    # Features
    path("features/", views.FeatureListView.as_view(), name="features"),
    path("features/maintenance/", views.MaintenanceView.as_view(), name="maintenance"),
    path("features/flags/new/", views.FlagCreateView.as_view(), name="flag_create"),
    path("features/flags/<int:pk>/edit/", views.FlagUpdateView.as_view(), name="flag_update"),
    path("features/switches/<int:pk>/toggle/", views.SwitchToggleView.as_view(), name="switch_toggle"),
    # Billing (platform charging the clubs)
    path("billing/", views.BillingView.as_view(), name="billing"),
    path("billing/plans/new/", views.PlanCreateView.as_view(), name="plan_create"),
    path("billing/plans/<uuid:pk>/edit/", views.PlanUpdateView.as_view(), name="plan_update"),
    path("billing/plans/<uuid:pk>/prices/new/", views.PlanPriceCreateView.as_view(), name="plan_price_create"),
    path("billing/dues/<uuid:pk>/pay/", views.RecordPaymentView.as_view(), name="due_pay"),
    path("billing/dues/<uuid:pk>/waive/", views.WaiveDueView.as_view(), name="due_waive"),
    path("billing/dues/<uuid:pk>/invoice.pdf", views.InvoicePdfView.as_view(), name="due_invoice"),
    path("clubs/<uuid:pk>/subscription/", views.SubscribeClubView.as_view(), name="club_subscribe"),
    path("clubs/<uuid:pk>/trial/start/", views.ClubStartTrialView.as_view(), name="club_trial_start"),
    path("clubs/<uuid:pk>/period/new/", views.OpenPeriodView.as_view(), name="club_open_period"),
    # Platform admins (superusers only)
    path("admins/", views.PlatformAdminListView.as_view(), name="admins"),
    path("admins/add/", views.PlatformAdminAddView.as_view(), name="admin_add"),
    path("admins/<uuid:pk>/update/", views.PlatformAdminUpdateView.as_view(), name="admin_update"),
    path("admins/<uuid:pk>/revoke/", views.PlatformAdminRevokeView.as_view(), name="admin_revoke"),
]
