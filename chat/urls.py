from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("chat/attachments/<uuid:pk>/", views.AttachmentView.as_view(), name="attachment"),
]
