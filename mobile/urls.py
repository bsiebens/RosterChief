from django.urls import path

from . import coach_views, views

app_name = "mobile"

urlpatterns = [
    # PWA plumbing.
    path("manifest.webmanifest", views.ManifestView.as_view(), name="manifest"),
    path("sw.js", views.ServiceWorkerView.as_view(), name="service_worker"),
    path("icon/<int:size>.png", views.AppIconView.as_view(), name="icon"),
    path("push/subscribe/", views.PushSubscribeView.as_view(), name="push_subscribe"),
    path("calendar/<str:token>.ics", views.CalendarFeedView.as_view(), name="calendar_feed"),
    # Member mode (M1-M7).
    path("", views.HomeView.as_view(), name="home"),
    path("calendar/", views.CalendarView.as_view(), name="calendar"),
    path("events/<uuid:pk>/", views.EventDetailView.as_view(), name="event_detail"),
    path("news/", views.NewsListView.as_view(), name="news_list"),
    path("news/<slug:slug>/", views.NewsDetailView.as_view(), name="news_detail"),
    path("me/", views.MeView.as_view(), name="me"),
    path("me/payments/", views.PaymentsView.as_view(), name="payments"),
    path("me/calendar-sync/", views.CalendarFeedSettingsView.as_view(), name="calendar_feed_settings"),
    path("me/<uuid:member_id>/edit/", views.EditProfileView.as_view(), name="edit_profile"),
    path("notifications/", views.NotificationsView.as_view(), name="notifications"),
    # Coach mode (C1-C6).
    path("coach/", coach_views.CoachTodayView.as_view(), name="coach_today"),
    path("coach/squad/", coach_views.CoachSquadView.as_view(), name="coach_squad"),
    path("coach/schedule/", coach_views.CoachScheduleView.as_view(), name="coach_schedule"),
    path("coach/attendance/<uuid:event_id>/", coach_views.CoachAttendanceView.as_view(), name="coach_attendance"),
    path("coach/events/new/", coach_views.CoachCreateEventView.as_view(), name="coach_create_event"),
    path("coach/news/new/", coach_views.CoachCreateNewsView.as_view(), name="coach_create_news"),
    path("coach/roster/add/", coach_views.CoachAddPlayerView.as_view(), name="coach_add_player"),
    path("coach/lineup/<uuid:event_id>/", coach_views.CoachLineupView.as_view(), name="coach_lineup"),
    path("coach/lineup/<uuid:event_id>/publish/", coach_views.CoachLineupPublishView.as_view(), name="coach_lineup_publish"),
]
