import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="cvs.tasks.run_generation", acks_late=True)
def run_generation_task(generation_id):
    from .services import run_generation

    gen = run_generation(generation_id)
    return gen.status


@shared_task(name="cvs.tasks.recover_stuck_generations")
def recover_stuck_generations():
    """Fail (and refund) generations stuck in processing, e.g. after a worker crash."""
    from .models import GenerationRequest
    from .services import _fail

    cutoff = timezone.now() - timedelta(minutes=15)
    stuck = GenerationRequest.objects.filter(status__in=[GenerationRequest.Status.PROCESSING, GenerationRequest.Status.QUEUED], created_at__lt=cutoff)
    count = 0
    for gen in stuck:
        _fail(gen, "This generation timed out. Your quota has been refunded — please try again.", "stuck")
        count += 1
    if count:
        logger.warning("Recovered %s stuck generations", count)
    return count
