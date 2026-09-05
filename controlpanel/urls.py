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
    path("features/email-suppression/", views.EmailSuppressionView.as_view(), name="email_suppression"),
    path("features/flags/new/", views.FlagCreateView.as_view(), name="flag_create"),
    path("features/flags/<int:pk>/edit/", views.FlagUpdateView.as_view(), name="flag_update"),
    path("features/switches/<int:pk>/toggle/", views.SwitchToggleView.as_view(), name="switch_toggle"),
    path("features/competitions/new/", views.CompetitionCreateView.as_view(), name="competition_create"),
    path("features/competitions/<int:pk>/edit/", views.CompetitionUpdateView.as_view(), name="competition_update"),
    path("features/competitions/<int:pk>/delete/", views.CompetitionDeleteView.as_view(), name="competition_delete"),
    # Billing (platform charging the clubs)
    path("billing/", views.BillingView.as_view(), name="billing"),
    path("billing/plans/new/", views.PlanCreateView.as_view(), name="plan_create"),
    path("billing/plans/<uuid:pk>/edit/", views.PlanUpdateView.as_view(), name="plan_update"),
    path("billing/plans/<uuid:pk>/delete/", views.PlanDeleteView.as_view(), name="plan_delete"),
    path("billing/plans/<uuid:pk>/prices/new/", views.PlanPriceCreateView.as_view(), name="plan_price_create"),
    path("billing/dues/<uuid:pk>/pay/", views.RecordPaymentView.as_view(), name="due_pay"),
    path("billing/dues/<uuid:pk>/waive/", views.WaiveDueView.as_view(), name="due_waive"),
    path("billing/dues/<uuid:pk>/invoice.pdf", views.InvoicePdfView.as_view(), name="due_invoice"),
    path("billing/dues/<uuid:pk>/invoice/send/", views.SendInvoiceView.as_view(), name="due_invoice_send"),
    path("billing/dues/<uuid:pk>/invoice/mark-sent/", views.MarkInvoiceSentView.as_view(), name="due_invoice_mark_sent"),
    path("clubs/<uuid:pk>/subscription/", views.SubscribeClubView.as_view(), name="club_subscribe"),
    path("clubs/<uuid:pk>/trial/start/", views.ClubStartTrialView.as_view(), name="club_trial_start"),
    path("clubs/<uuid:pk>/period/new/", views.OpenPeriodView.as_view(), name="club_open_period"),
    # Bugs
    path("bugs/", views.BugListView.as_view(), name="bug_list"),
    path("bugs/<uuid:pk>/", views.BugDetailView.as_view(), name="bug_detail"),
    path("bugs/<uuid:pk>/update/", views.BugUpdateView.as_view(), name="bug_update"),
    path("bugs/<uuid:pk>/note/", views.BugAddNoteView.as_view(), name="bug_add_note"),
    # Announcements (superusers only)
    path("announcements/", views.AnnouncementListView.as_view(), name="announcement_list"),
    path("announcements/new/", views.AnnouncementComposeView.as_view(), name="announcement_compose"),
    path("announcements/<uuid:pk>/cancel/", views.AnnouncementCancelView.as_view(), name="announcement_cancel"),
    # Platform admins (superusers only)
    path("admins/", views.PlatformAdminListView.as_view(), name="admins"),
    path("admins/add/", views.PlatformAdminAddView.as_view(), name="admin_add"),
    path("admins/<uuid:pk>/update/", views.PlatformAdminUpdateView.as_view(), name="admin_update"),
    path("admins/<uuid:pk>/revoke/", views.PlatformAdminRevokeView.as_view(), name="admin_revoke"),
    # Jobs (cron's scheduled platform jobs -- see controlpanel/services/jobs.py)
    path("jobs/", views.JobsView.as_view(), name="jobs"),
    path("jobs/<str:name>/toggle/", views.JobToggleView.as_view(), name="job_toggle"),
    path("jobs/<str:name>/run/", views.JobRunNowView.as_view(), name="job_run_now"),
]
