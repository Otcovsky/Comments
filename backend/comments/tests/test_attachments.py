from io import BytesIO
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image

from comments import attachments
from comments.attachments import AttachmentError, process
from comments.models import Comment


def image_bytes(image_format, size=(800, 600), mode="RGB", **options):
    buffer = BytesIO()
    Image.new(mode, size, "red").save(buffer, format=image_format, **options)
    return buffer.getvalue()


def gif_bytes(frames, size=(40, 30), **options):
    images = [Image.new("RGB", size, (index * 10 % 255, 0, 0)) for index in range(frames)]
    buffer = BytesIO()
    images[0].save(buffer, format="GIF", save_all=True, append_images=images[1:], duration=50, **options)
    return buffer.getvalue()


def gif_frame(size):
    """A one-frame GIF split into its header with the palette and the frame itself."""
    buffer = BytesIO()
    Image.new("L", size, 7).convert("P").save(buffer, format="GIF")
    content = buffer.getvalue()
    frame_start = content.index(b"\x2c", 13 + 3 * 2 ** ((content[10] & 7) + 1))
    return content[:frame_start], content[frame_start:-1]


def gif_with_large_second_frame(size):
    """The header says 1x1 and the second frame is larger, which Pillow accepts."""
    header, large_frame = gif_frame(size)
    small_frame = gif_frame((1, 1))[1]
    return header[:6] + b"\x01\x00\x01\x00" + header[10:] + small_frame + large_frame + b"\x3b"


def upload(name, content):
    return SimpleUploadedFile(name, content)


def opened(file):
    return Image.open(BytesIO(file.content))


class ImageTests(SimpleTestCase):
    def test_jpeg_is_shrunk_to_fit(self):
        file = process(upload("a.jpg", image_bytes("JPEG")))
        self.assertEqual(opened(file).size, (320, 240))
        self.assertEqual(opened(file).format, "JPEG")
        self.assertEqual((file.extension, file.content_type), ("jpg", "image/jpeg"))
        self.assertEqual(file.kind, Comment.AttachmentType.IMAGE)

    def test_proportions_are_kept(self):
        self.assertEqual(opened(process(upload("a.png", image_bytes("PNG", (1000, 100))))).size, (320, 32))

    def test_small_image_is_not_enlarged(self):
        self.assertEqual(opened(process(upload("a.png", image_bytes("PNG", (50, 40))))).size, (50, 40))

    def test_png_stays_png(self):
        file = process(upload("a.png", image_bytes("PNG", mode="RGBA")))
        self.assertEqual(opened(file).format, "PNG")
        self.assertEqual(opened(file).mode, "RGBA")
        self.assertEqual(file.content_type, "image/png")

    def test_palette_png(self):
        self.assertEqual(opened(process(upload("a.png", image_bytes("PNG", mode="P")))).size, (320, 240))

    def test_format_comes_from_the_content(self):
        file = process(upload("photo.png", image_bytes("JPEG")))
        self.assertEqual(file.extension, "jpg")

    def test_exif_orientation_is_applied(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # rotated 90 degrees
        content = image_bytes("JPEG", (800, 400), exif=exif)
        file = process(upload("a.jpg", content))
        self.assertEqual(opened(file).size, (120, 240))
        self.assertNotIn(0x0112, opened(file).getexif())

    def test_animated_gif_keeps_its_frames(self):
        file = process(upload("a.gif", gif_bytes(5, (640, 480), loop=0)))
        result = opened(file)
        self.assertEqual(result.n_frames, 5)
        self.assertEqual(result.size, (320, 240))
        self.assertEqual(result.info["loop"], 0)
        self.assertEqual(file.content_type, "image/gif")

    def test_gif_that_plays_once_is_not_looped(self):
        self.assertNotIn("loop", opened(process(upload("a.gif", gif_bytes(3)))).info)

    def test_still_gif(self):
        self.assertEqual(opened(process(upload("a.gif", image_bytes("GIF")))).size, (320, 240))

    def test_too_many_gif_frames(self):
        with mock.patch.object(attachments, "MAX_GIF_FRAMES", 3), self.assertRaises(AttachmentError):
            process(upload("a.gif", gif_bytes(4)))

    def test_gif_frame_larger_than_its_header_is_too_large(self):
        content = gif_with_large_second_frame((100, 100))
        with mock.patch.object(attachments, "MAX_SOURCE_PIXELS", 5_000), self.assertRaises(AttachmentError):
            process(upload("a.gif", content))

    def test_gif_frame_larger_than_its_header_counts_towards_the_total(self):
        content = gif_with_large_second_frame((100, 100))
        with mock.patch.object(attachments, "MAX_GIF_PIXELS", 5_000), self.assertRaises(AttachmentError):
            process(upload("a.gif", content))

    def test_too_many_gif_pixels(self):
        with mock.patch.object(attachments, "MAX_GIF_PIXELS", 40 * 30 * 3), self.assertRaises(AttachmentError):
            process(upload("a.gif", gif_bytes(4)))

    def test_file_too_large(self):
        with mock.patch.object(attachments, "MAX_RAW_IMAGE_BYTES", 10), self.assertRaises(AttachmentError):
            process(upload("a.png", image_bytes("PNG")))

    def test_too_many_pixels(self):
        with mock.patch.object(attachments, "MAX_SOURCE_PIXELS", 100), self.assertRaises(AttachmentError):
            process(upload("a.png", image_bytes("PNG")))

    def test_not_an_image(self):
        with self.assertRaises(AttachmentError):
            process(upload("a.png", b"<script>alert(1)</script>"))

    def test_other_image_format_with_a_good_name(self):
        with self.assertRaises(AttachmentError):
            process(upload("a.jpg", image_bytes("BMP")))

    def test_truncated_image(self):
        with self.assertRaises(AttachmentError):
            process(upload("a.jpg", image_bytes("JPEG")[:400]))

    def test_other_extension(self):
        with self.assertRaises(AttachmentError):
            process(upload("a.exe", image_bytes("PNG")))
        with self.assertRaises(AttachmentError):
            process(upload("noextension", image_bytes("PNG")))


class TextTests(SimpleTestCase):
    def test_stored_unchanged(self):
        content = "<b>привет</b>\tмир\r\n\f".encode()
        file = process(upload("a.txt", content))
        self.assertEqual(file.content, content)
        self.assertEqual((file.extension, file.content_type), ("txt", "text/plain; charset=utf-8"))
        self.assertEqual(file.kind, Comment.AttachmentType.TEXT)

    def test_bom_is_removed(self):
        self.assertEqual(process(upload("a.txt", b"\xef\xbb\xbfhello")).content, b"hello")

    def test_extension_ignores_case(self):
        self.assertEqual(process(upload("A.TXT", b"hello")).extension, "txt")

    def test_size_limit(self):
        self.assertEqual(len(process(upload("a.txt", b"a" * 102400)).content), 102400)
        with self.assertRaises(AttachmentError):
            process(upload("a.txt", b"a" * 102401))

    def test_other_encoding(self):
        with self.assertRaises(AttachmentError):
            process(upload("a.txt", "привет".encode("cp1251")))

    def test_control_characters(self):
        for content in (b"a\x00b", b"a\x08b", b"a\x0bb", b"a\x1fb", b"a\x7fb"):
            with self.subTest(content), self.assertRaises(AttachmentError):
                process(upload("a.txt", content))

    def test_empty(self):
        for content in (b"", b"  \n\t ", b"\xef\xbb\xbf"):
            with self.subTest(content), self.assertRaises(AttachmentError):
                process(upload("a.txt", content))
