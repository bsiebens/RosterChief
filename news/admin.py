from django.contrib import admin

from .models import News, NewsPhoto


class NewsPhotoInline(admin.TabularInline):
    model = NewsPhoto
    extra = 0


@admin.register(News)
class NewsAdmin(admin.ModelAdmin):
    list_display = ["title", "club", "status", "visibility", "created_by"]
    list_filter = ["club", "status", "visibility"]
    search_fields = ["title", "title_en"]
    raw_id_fields = ["created_by"]
    inlines = [NewsPhotoInline]


@admin.register(NewsPhoto)
class NewsPhotoAdmin(admin.ModelAdmin):
    list_display = ["news_item", "is_main", "ordering"]
    list_filter = ["is_main"]
