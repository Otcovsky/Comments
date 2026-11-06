"""Server rules for the form fields; frontend/src/validation.js repeats them for the user."""
import re

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email

from .errors import ApiError
from .sanitizer import MessageSanitizer, SanitizeError

MAX_USERNAME = 64
MAX_EMAIL = 254
MAX_HOME_PAGE = 200
MAX_TEXT = 5000

LATIN_ALNUM = re.compile(r"[A-Za-z0-9]+")
validate_home_page = URLValidator(schemes=["http", "https"])


def parse_id(value):
    """The number inside a string id, or None. Ids fit a BIGINT, so 19 digits are enough."""
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 19:
        return int(value)
    return None


def clean_text(text):
    """Returns the sanitized text; raises ValueError with the message for the user."""
    if not text.strip():
        raise ValueError("Обязательное поле.")
    if len(text) > MAX_TEXT:
        raise ValueError(f"Не больше {MAX_TEXT} символов.")
    try:
        return MessageSanitizer.clean(text)
    except SanitizeError as error:
        raise ValueError(str(error))


def preview_text(text):
    try:
        return clean_text(text)
    except ValueError as error:
        raise ApiError("VALIDATION", {"text": str(error)})


def check_username(username):
    if not username:
        return "Обязательное поле."
    if len(username) > MAX_USERNAME:
        return f"Не больше {MAX_USERNAME} символов."
    return None if LATIN_ALNUM.fullmatch(username) else "Только латинские буквы и цифры."


def check_email(email):
    if not email:
        return "Обязательное поле."
    if len(email) > MAX_EMAIL:
        return f"Не больше {MAX_EMAIL} символов."
    try:
        validate_email(email)
    except ValidationError:
        return "Некорректный e-mail."
    return None


def check_home_page(home_page):
    if not home_page:
        return None
    if len(home_page) > MAX_HOME_PAGE:
        return f"Не больше {MAX_HOME_PAGE} символов."
    try:
        validate_home_page(home_page)
    except ValidationError:
        return "Некорректный адрес: он должен начинаться с http:// или https://."
    return None
