"""Management API jobs of the shop (``api.core.jobs``): started by api/panel/routers/shop_orders.py and
polled at ``GET /manage/jobs/{id}``. They live here so the Celery worker's autodiscovery registers them
without importing the API."""

from django.utils.translation import gettext as _

from api.core.errors import ApiError
from api.core.jobs import JobContext, job_task


@job_task(name="shop.export-production")
def export_production_job(ctx: JobContext, product_ids: list[str]):
    from shop.services.orders import export_pending_production, merchandise_products

    exported = export_pending_production(ctx.club, merchandise_products(ctx.club).filter(pk__in=product_ids))
    if exported is None:
        raise ApiError(422, "nothing_to_export", _("No pending items for the selected products."))
    token, filename, count = exported
    return {"token": token, "filename": filename, "lines": count}


@job_task(name="shop.bulk-mark-paid")
def bulk_mark_paid_job(ctx: JobContext, order_ids: list[str], method: str):
    from shop.services.orders import bulk_mark_paid

    return {"updated": bulk_mark_paid(ctx.club, order_ids, method=method)}


@job_task(name="shop.bulk-mark-ready-for-pickup")
def bulk_mark_ready_for_pickup_job(ctx: JobContext, order_ids: list[str], pickup_instructions: str):
    from shop.services.orders import bulk_mark_ready_for_pickup

    return {"updated": bulk_mark_ready_for_pickup(ctx.club, order_ids, pickup_instructions=pickup_instructions)}
