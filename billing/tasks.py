from celery import shared_task


@shared_task(name="billing.tasks.reconcile")
def reconcile():
    from .providers import BillingNotConfigured
    from .reconcile import reconcile_subscriptions

    try:
        return reconcile_subscriptions()
    except BillingNotConfigured:
        return "billing not configured"
