"""Celery tasks, run by the worker service (docker-compose.yml) from the RabbitMQ queue.

- send_events: who sees a new comment, and the events for them (live.comment_created). The
  web processes only queue it, so a burst of comments waits in the queue instead of piling up
  on their DB threads, and the number of fan-outs at once is the worker's concurrency.
- delete_attachment: a file stored in the bucket for a comment that was then not saved.

Both may run twice (CELERY_TASK_ACKS_LATE): an event twice makes a client reload once more,
and a file deleted twice is still gone.
"""
import asyncio
import logging
import threading

from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task

from . import live, storage

logger = logging.getLogger(__name__)

EVENTS_TIMEOUT_SECONDS = 20

_loop = None
_loop_lock = threading.Lock()


def _run(coroutine):
    """Runs a coroutine on this process's own event loop, which lives as long as the process:
    the channel layer and the asyncio Redis client keep their connections in it."""
    global _loop
    with _loop_lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, name="events-loop", daemon=True).start()
    return asyncio.run_coroutine_threadsafe(coroutine, _loop).result(EVENTS_TIMEOUT_SECONDS)


@shared_task
def send_events(comment, parent):
    _run(live.comment_created(comment, parent))


@shared_task(autoretry_for=(BotoCoreError, ClientError), retry_backoff=5, max_retries=8)
def delete_attachment(key):
    storage.delete(key)


async def queue(task, *args):
    """Puts a task in the queue from the event loop: publishing blocks, so it runs in a thread.
    Raises what the broker raised."""
    await asyncio.to_thread(task.delay, *args)


async def delete_attachment_later(key):
    try:
        await queue(delete_attachment, key)
    except Exception:
        logger.exception("Attachment %s stays in the bucket: the delete could not be queued", key)
