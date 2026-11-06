import json

from channels.generic.websocket import AsyncWebsocketConsumer

from .live import branch_group, parse_viewport, viewers

MAX_MESSAGE_CHARS = 32 * 1024  # 200 branches fit several times over


class CommentsConsumer(AsyncWebsocketConsumer):
    """An anonymous client reports what is on its screen and gets events about that zone."""

    async def connect(self):
        self.branch_groups = set()
        await self.accept()

    async def disconnect(self, code):
        viewers.drop(self.channel_name)
        await self._join_groups(set())

    async def receive(self, text_data=None, bytes_data=None):
        # A bad message is ignored and the previous zone stays in force.
        if not text_data or len(text_data) > MAX_MESSAGE_CHARS:
            return
        try:
            viewport = parse_viewport(json.loads(text_data))
        except (ValueError, RecursionError):  # the second one is JSON nested too deep
            return
        if viewport is None:
            return
        ordering, page_size, page, branch_ids = viewport
        viewers.show(self.channel_name, ordering, page_size, page)
        await self._join_groups({branch_group(branch_id) for branch_id in branch_ids})

    async def _join_groups(self, groups):
        for group in groups - self.branch_groups:
            await self.channel_layer.group_add(group, self.channel_name)
        for group in self.branch_groups - groups:
            await self.channel_layer.group_discard(group, self.channel_name)
        self.branch_groups = groups

    async def comment_event(self, event):
        await self.send(text_data=json.dumps(event["payload"]))
