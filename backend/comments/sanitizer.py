"""Validation and sanitizing of comment text.

Only <a href title>, <code>, <i>, <strong> are allowed. Tags must be properly
nested and closed (valid XHTML); everything else is rejected. The output is
rebuilt from parsed tokens, so text and attribute values are always escaped.
"""
import re
from html import escape
from html.parser import HTMLParser


# What the parser leaves unread when the text ends inside a tag. Python versions treat it
# differently: some keep it as text, others drop it silently, so it is rejected here.
UNFINISHED_MARKUP_RE = re.compile(r"<[a-zA-Z/!?]")


class SanitizeError(ValueError):
    pass


class MessageSanitizer(HTMLParser):
    ALLOWED_TAGS = {
        "a": {"href", "title"},
        "code": set(),
        "i": set(),
        "strong": set(),
    }
    REQUIRED_ATTRS = {"a": {"href"}}
    URL_RE = re.compile(r"^(https?://|mailto:)[^\s\"'<>`]+$", re.IGNORECASE)

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._stack: list[str] = []

    @classmethod
    def clean(cls, text: str) -> str:
        sanitizer = cls()
        try:
            sanitizer.feed(text)
        except AssertionError:
            # How older Python versions report some of the markup after "<![".
            raise SanitizeError("Некорректная разметка.")
        if UNFINISHED_MARKUP_RE.match(sanitizer.rawdata):
            raise SanitizeError("Тег в конце текста не завершён. Знак «<» можно записать как &lt;.")
        sanitizer.close()
        if sanitizer._stack:
            raise SanitizeError(f"Тег <{sanitizer._stack[-1]}> не закрыт.")
        return "".join(sanitizer._out)

    def handle_starttag(self, tag, attrs):
        if tag not in self.ALLOWED_TAGS:
            raise SanitizeError(f"Тег <{tag}> запрещён. Разрешены: a, code, i, strong.")
        allowed = self.ALLOWED_TAGS[tag]
        seen = {}
        for name, value in attrs:
            if name not in allowed:
                raise SanitizeError(f"Атрибут «{name}» запрещён для тега <{tag}>.")
            if value is None:
                raise SanitizeError(f"Атрибут «{name}» должен иметь значение.")
            if name in seen:
                raise SanitizeError(f"Атрибут «{name}» указан дважды.")
            seen[name] = value
        missing = self.REQUIRED_ATTRS.get(tag, set()) - seen.keys()
        if missing:
            raise SanitizeError(f"У тега <{tag}> нет обязательного атрибута «{missing.pop()}».")
        if tag == "a" and "a" in self._stack:
            raise SanitizeError("Ссылку нельзя вкладывать в другую ссылку.")
        if tag == "a" and not self.URL_RE.match(seen["href"].strip()):
            raise SanitizeError("Ссылка должна начинаться с http://, https:// или mailto:.")

        rendered = "".join(f' {name}="{escape(value.strip(), quote=True)}"' for name, value in seen.items())
        self._stack.append(tag)
        self._out.append(f"<{tag}{rendered}>")

    def handle_endtag(self, tag):
        if not self._stack:
            raise SanitizeError(f"Закрывающий тег </{tag}> без открывающего.")
        expected = self._stack.pop()
        if tag != expected:
            raise SanitizeError(f"Ожидался </{expected}>, а найден </{tag}>.")
        self._out.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        raise SanitizeError(f"Самозакрывающийся тег <{tag}/> не разрешён.")

    def handle_data(self, data):
        self._out.append(escape(data, quote=False))

    def handle_comment(self, data):
        raise SanitizeError("HTML-комментарии не разрешены.")

    def handle_decl(self, decl):
        raise SanitizeError("Декларации не разрешены.")

    def handle_pi(self, data):
        raise SanitizeError("Инструкции обработки не разрешены.")

    def unknown_decl(self, data):
        raise SanitizeError("CDATA не разрешена.")
