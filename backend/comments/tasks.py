"""Celery tasks, run by the worker service (docker-compose.yml) from the RabbitMQ queue.

delete_attachment: a file stored in the bucket for a comment that was then not saved. It is
retried while the bucket fails, and may run twice (CELERY_TASK_ACKS_LATE): a file deleted
twice is still gone.

The events of new comments were sent from here too, but the worker and RabbitMQ on the same
two cores as the web processes cost more CPU than they took off them (capacity 192 -> 160
it/s, perf/results/OPTIMIZATIONS.md, section 7), so the web processes send them (live.py).
"""
import asyncio
import logging

from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task

from . import storage

logger = logging.getLogger(__name__)


@shared_task(autoretry_for=(BotoCoreError, ClientError), retry_backoff=5, max_retries=8)
def delete_attachment(key):
    storage.delete(key)


async def delete_attachment_later(key):
    """Queues the delete from the event loop: publishing blocks, so it runs in a thread."""
    try:
        await asyncio.to_thread(delete_attachment.delay, key)
    except Exception:
        logger.exception("Attachment %s stays in the bucket: the delete could not be queued", key)
