import os

import django
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core import signals
from django.core.handlers.asgi import ASGIHandler
from django.db import close_old_connections, reset_queries

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup(set_prefix=False)

from comments.routing import websocket_urlpatterns  # noqa: E402  (needs the app registry)


class SharedThreadASGIHandler(ASGIHandler):
    """Django's handler without a thread per request.

    Django opens a ThreadSensitiveContext for every request, and the first sync call in it
    (the receivers of request_started, then response.close) starts a new thread, plus one more
    to shut it down: about a tenth of the CPU at 160 it/s (perf/results/OPTIMIZATIONS.md,
    diagnosis 3). Without the context those calls run in asgiref's one thread per process.
    They are not ours anyway: the views are async, and the database is used only from the
    threads of comments/pools.py."""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            raise ValueError(f"Django can only handle ASGI/HTTP connections, not {scope['type']}.")
        await self.handle(scope, receive, send)


# These look after the connection of the request's own thread, which never touches the
# database; comments/pools.py closes old connections in its threads. Without them
# request_started has no sync receivers at all.
signals.request_started.disconnect(reset_queries)
signals.request_started.disconnect(close_old_connections)
signals.request_finished.disconnect(close_old_connections)

application = ProtocolTypeRouter(
    {
        "http": SharedThreadASGIHandler(),
        "websocket": AllowedHostsOriginValidator(URLRouter(websocket_urlpatterns)),
    }
)
