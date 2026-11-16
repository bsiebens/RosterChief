from django.core.cache import cache
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.http import require_safe
from redis.exceptions import RedisError


@require_safe
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        cache.get("rosterchief:health")
    except (DatabaseError, RedisError, OSError):
        return JsonResponse({"status": "unavailable"}, status=503, headers={"Cache-Control": "no-store"})
    return JsonResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})
