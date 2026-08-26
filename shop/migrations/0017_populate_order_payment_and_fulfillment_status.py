from django.db import migrations

#: The old single Order.status value, mapped onto the two new independent
#: axes. Best-effort starting guess only -- populate_status_fields below
#: cross-checks against actual Payment rows and corrects the payment side,
#: since the old status was sometimes set by hand (e.g. a manual .save()
#: toggle) without a matching Payment ever being created.
STATUS_TO_PAYMENT = {
    'pending': 'pending',
    'partially_paid': 'partially_paid',
    'paid': 'paid',
    'refunded': 'refunded',
    'ready_for_pickup': 'pending',
    'cancelled': 'pending',
    'delivered': 'paid',
}
STATUS_TO_FULFILLMENT = {
    'pending': 'not_ready',
    'partially_paid': 'not_ready',
    'paid': 'not_ready',
    'refunded': 'not_ready',
    'ready_for_pickup': 'ready_for_pickup',
    'cancelled': 'cancelled',
    'delivered': 'delivered',
}


def populate_status_fields(apps, schema_editor):
    Order = apps.get_model('shop', 'Order')
    Payment = apps.get_model('shop', 'Payment')

    for order in Order.objects.all():
        payment_status = STATUS_TO_PAYMENT.get(order.status, 'pending')
        fulfillment_status = STATUS_TO_FULFILLMENT.get(order.status, 'not_ready')

        has_confirmed_payment = Payment.objects.filter(order=order, status='confirmed').exists()
        if payment_status == 'paid' and not has_confirmed_payment:
            payment_status = 'pending'
        elif payment_status == 'pending' and has_confirmed_payment:
            payment_status = 'paid'

        Order.objects.filter(pk=order.pk).update(payment_status=payment_status, fulfillment_status=fulfillment_status)


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0016_order_payment_status_order_fulfillment_status'),
    ]

    operations = [
        migrations.RunPython(populate_status_fields, migrations.RunPython.noop),
    ]
