import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

from .models import Comment

MAX_IMAGE_SIZE = (320, 240)
MAX_RAW_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT_BYTES = 100 * 1024
# A small file can still decode into a huge bitmap or thousands of frames.
MAX_SOURCE_PIXELS = 20_000_000
MAX_GIF_FRAMES = 200
MAX_GIF_PIXELS = 200_000_000  # all frames together
JPEG_QUALITY = 90

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif"}
# Pillow format -> format to save in, extension, Content-Type. MPO is a JPEG with extra frames,
# which is how some phones save photos.
IMAGE_FORMATS = {
    "JPEG": ("JPEG", "jpg", "image/jpeg"),
    "MPO": ("JPEG", "jpg", "image/jpeg"),
    "PNG": ("PNG", "png", "image/png"),
    "GIF": ("GIF", "gif", "image/gif"),
}
RESIZABLE_MODES = ("L", "LA", "RGB", "RGBA")
# Only \t, \n, \f and \r are allowed among the control characters.
CONTROL_CHARACTERS = re.compile("[\x00-\x08\x0b\x0e-\x1f\x7f]")

BAD_IMAGE = "Файл не является корректным изображением."


class AttachmentError(ValueError):
    pass


@dataclass
class ProcessedFile:
    content: bytes
    extension: str
    content_type: str
    kind: str


def process(upload):
    """Checks an uploaded file and returns what goes to the bucket. Nothing here trusts the
    browser's checks: an image is judged by its content, and only its small version is kept."""
    extension = Path(upload.name or "").suffix.lower()
    if extension == ".txt":
        return _process_text(upload)
    if extension in IMAGE_EXTENSIONS:
        return _process_image(upload)
    raise AttachmentError("Допустимые форматы: JPG, GIF, PNG или TXT.")


def _process_text(upload):
    if upload.size > MAX_TEXT_BYTES:
        raise AttachmentError("Текстовый файл не должен быть больше 100 КБ.")
    try:
        text = upload.read().decode("utf-8-sig")  # drops a BOM, keeps the rest as it is
    except UnicodeDecodeError:
        raise AttachmentError("Файл должен быть текстом в кодировке UTF-8.")
    if CONTROL_CHARACTERS.search(text):
        raise AttachmentError("Файл содержит двоичные данные, а не текст.")
    if not text.strip():
        raise AttachmentError("Файл пуст.")
    return ProcessedFile(text.encode("utf-8"), "txt", "text/plain; charset=utf-8", Comment.AttachmentType.TEXT)


def _process_image(upload):
    if upload.size > MAX_RAW_IMAGE_BYTES:
        raise AttachmentError("Изображение слишком большое (максимум 5 МБ).")
    try:
        image = Image.open(upload, formats=("JPEG", "PNG", "GIF"))
        save_format, extension, content_type = IMAGE_FORMATS[image.format]
        # The header is already parsed, so the size is known before decoding.
        if image.width * image.height > MAX_SOURCE_PIXELS:
            raise AttachmentError("Разрешение изображения слишком большое.")
        buffer = BytesIO()
        if save_format == "GIF" and getattr(image, "is_animated", False):
            _save_animated_gif(image, buffer)
        else:
            _save_still(image, save_format, buffer)
    except AttachmentError:
        raise
    except Exception:
        # Decoders fail in many ways on bad data, and every one of them means the same here.
        raise AttachmentError(BAD_IMAGE)
    return ProcessedFile(buffer.getvalue(), extension, content_type, Comment.AttachmentType.IMAGE)


def _fit(image):
    # A palette would be resized without smoothing.
    image = image.copy() if image.mode in RESIZABLE_MODES else image.convert("RGBA")
    image.thumbnail(MAX_IMAGE_SIZE, Image.LANCZOS)
    return image


def _save_still(image, save_format, buffer):
    # Re-encoding drops EXIF, so its orientation is applied to the pixels first.
    image = _fit(ImageOps.exif_transpose(image))
    options = {}
    if save_format == "JPEG":
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        options["quality"] = JPEG_QUALITY
    image.save(buffer, format=save_format, **options)


def _save_animated_gif(image, buffer):
    frames, durations = [], []
    pixels_per_frame = image.width * image.height
    for index, frame in enumerate(ImageSequence.Iterator(image)):
        if index >= MAX_GIF_FRAMES:
            raise AttachmentError(f"В анимации слишком много кадров (максимум {MAX_GIF_FRAMES}).")
        if (index + 1) * pixels_per_frame > MAX_GIF_PIXELS:
            raise AttachmentError("Анимация слишком большая: уменьшите размер или число кадров.")
        frames.append(_fit(frame.convert("RGBA")))
        # Frames may have different delays; keep each one.
        durations.append(frame.info.get("duration", 100))
    options = {"loop": image.info["loop"]} if "loop" in image.info else {}
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        disposal=2,
        **options,
    )
