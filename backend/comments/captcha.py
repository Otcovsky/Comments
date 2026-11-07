import asyncio
import base64
import random
import secrets
import time
import uuid
from collections import OrderedDict
from io import BytesIO

from django.conf import settings
from PIL import Image, ImageDraw, ImageFilter, ImageFont

LENGTH = 5
TTL_SECONDS = 5 * 60
# Enough for a burst; past it the oldest pictures stop working first.
MAX_ACTIVE = 10_000
# Ambiguous characters (0/O, 1/l/I) are excluded.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
SIZE = (160, 56)


class CaptchaStore:
    """Issued captchas in process memory. They live for minutes, so a restart costs a user one
    new picture, and no table has to be cleaned up. Only the event loop touches the store, so
    taking a key is atomic: of two parallel requests with the same key, one gets nothing."""

    def __init__(self):
        self._active = OrderedDict()  # key -> (answer, expiry), oldest first

    def add(self, answer):
        now = time.monotonic()
        while self._active and (
            len(self._active) >= MAX_ACTIVE or next(iter(self._active.values()))[1] <= now
        ):
            self._active.popitem(last=False)
        key = uuid.uuid4().hex
        self._active[key] = (answer, now + TTL_SECONDS)
        return key

    def check(self, key, value):
        """Spends the captcha, whatever the answer."""
        answer, expiry = self._active.pop(key, (None, 0))
        if answer is None or expiry <= time.monotonic():
            return False
        return secrets.compare_digest(answer.lower().encode(), value.lower().encode())


store = CaptchaStore()


async def issue():
    # A load test knows the answer in advance; the picture is still drawn and the key single-use.
    answer = settings.CAPTCHA_TEST_ANSWER or "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    image = await asyncio.to_thread(render, answer)
    return store.add(answer), image


def render(answer):
    rng = random.Random()
    width, height = SIZE
    image = Image.new("RGB", SIZE, (244, 245, 248))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=30)

    for _ in range(6):
        draw.line(
            [(rng.randint(0, width), rng.randint(0, height)) for _ in range(2)],
            fill=(rng.randint(150, 200),) * 3,
            width=1,
        )

    step = (width - 20) // len(answer)
    for index, char in enumerate(answer):
        glyph = Image.new("RGBA", (40, 50), (0, 0, 0, 0))
        color = (rng.randint(20, 90), rng.randint(20, 90), rng.randint(60, 140))
        ImageDraw.Draw(glyph).text((6, 4), char, font=font, fill=color)
        glyph = glyph.rotate(rng.uniform(-25, 25), resample=Image.BICUBIC, expand=False)
        image.paste(glyph, (10 + index * step, rng.randint(0, 8)), glyph)

    for _ in range(120):
        draw.point((rng.randint(0, width - 1), rng.randint(0, height - 1)), fill=(120, 120, 140))

    image = image.filter(ImageFilter.SMOOTH)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
