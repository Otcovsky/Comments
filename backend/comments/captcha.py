import asyncio
import base64
import random
import secrets
import uuid
from io import BytesIO

from django.conf import settings
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import shared

LENGTH = 5
TTL_SECONDS = 5 * 60
KEY_LENGTH = 32  # uuid4().hex
# Ambiguous characters (0/O, 1/l/I) are excluded.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
SIZE = (160, 56)


class CaptchaStore:
    """Issued captchas in Redis, so that any backend process can check one another process
    issued. They live for minutes and expire on their own, so no table has to be cleaned up.
    Taking a key is atomic: of two parallel requests with the same key, one gets nothing."""

    async def add(self, answer):
        key = uuid.uuid4().hex
        await shared.aclient().set(_redis_key(key), answer, ex=TTL_SECONDS)
        return key

    async def check(self, key, value):
        """Spends the captcha, whatever the answer."""
        if len(key) != KEY_LENGTH:  # not one of ours, and Redis keys stay short
            return False
        answer = await shared.aclient().getdel(_redis_key(key))
        if answer is None:
            return False
        return secrets.compare_digest(answer.lower().encode(), value.lower().encode())


def _redis_key(key):
    return f"captcha:{key}"


store = CaptchaStore()


async def issue():
    # A load test knows the answer in advance; the picture is still drawn and the key single-use.
    answer = settings.CAPTCHA_TEST_ANSWER or "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    image = await asyncio.to_thread(render, answer)
    return await store.add(answer), image


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
