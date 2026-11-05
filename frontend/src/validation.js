// Client-side checks mirror the server rules; the server stays the source of truth.

const LATIN_ALNUM = /^[A-Za-z0-9]+$/;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const HOME_PAGE = /^https?:\/\/[^\s/$.?#][^\s]*$/i;
const LINK_HREF = /^(https?:\/\/|mailto:)[^\s"'<>`]+$/i;
// Quoted attribute values may contain ">"; values may be quoted either way or unquoted, as on the server.
const TAG = /<\/?([a-zA-Z][a-zA-Z0-9]*)((?:[^<>"']|"[^"]*"|'[^']*')*)>/g;
const ATTRIBUTE = /([a-zA-Z-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))/g;
const ALLOWED_TAGS = { a: ["href", "title"], code: [], i: [], strong: [] };

export const MAX_TEXT_FILE_BYTES = 100 * 1024;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "gif"];

export const isTextFile = (file) => file.name.toLowerCase().endsWith(".txt");

export function checkTags(text) {
  if (text.includes("<!--")) return "HTML-комментарии не разрешены.";
  if (/<[!?]/.test(text)) return "Декларации и инструкции обработки не разрешены.";

  const stack = [];
  for (const [whole, rawName, rawAttrs] of text.matchAll(TAG)) {
    const name = rawName.toLowerCase();
    if (!(name in ALLOWED_TAGS)) return `Тег <${name}> запрещён. Разрешены: a, code, i, strong.`;

    if (whole.startsWith("</")) {
      const open = stack.pop();
      if (!open) return `Закрывающий тег </${name}> без открывающего.`;
      if (open !== name) return `Ожидался </${open}>, а найден </${name}>.`;
      continue;
    }
    if (rawAttrs.trim().endsWith("/")) return `Самозакрывающийся тег <${name}/> не разрешён.`;
    if (rawAttrs.replace(ATTRIBUTE, "").trim()) return `Некорректные атрибуты в теге <${name}>.`;

    const attrs = {};
    for (const [, rawKey, ...values] of rawAttrs.matchAll(ATTRIBUTE)) {
      const key = rawKey.toLowerCase();
      if (!ALLOWED_TAGS[name].includes(key)) return `Атрибут «${key}» запрещён для тега <${name}>.`;
      if (key in attrs) return `Атрибут «${key}» указан дважды.`;
      attrs[key] = values.find((value) => value !== undefined);
    }
    if (name === "a" && stack.includes("a")) return "Ссылку нельзя вкладывать в другую ссылку.";
    if (name === "a" && !LINK_HREF.test((attrs.href || "").trim())) {
      return "Ссылка должна начинаться с http://, https:// или mailto:.";
    }
    stack.push(name);
  }
  return stack.length ? `Тег <${stack.at(-1)}> не закрыт.` : null;
}

export function validateComment({ username, email, homePage, text }) {
  const errors = {};
  if (!username.trim()) errors.username = "Обязательное поле.";
  else if (!LATIN_ALNUM.test(username.trim())) errors.username = "Только латинские буквы и цифры.";

  if (!email.trim()) errors.email = "Обязательное поле.";
  else if (!EMAIL.test(email.trim())) errors.email = "Некорректный e-mail.";

  if (homePage.trim() && !HOME_PAGE.test(homePage.trim())) {
    errors.homePage = "Адрес должен начинаться с http:// или https://.";
  }

  if (!text.trim()) errors.text = "Обязательное поле.";
  else {
    const tagError = checkTags(text);
    if (tagError) errors.text = tagError;
  }
  return errors;
}

export function validateCaptcha(captcha) {
  if (!captcha.trim()) return "Введите код с картинки.";
  return LATIN_ALNUM.test(captcha.trim()) ? null : "Только латинские буквы и цифры.";
}

export function validateFile(file) {
  const extension = file.name.split(".").pop().toLowerCase();
  if (extension === "txt") {
    return file.size > MAX_TEXT_FILE_BYTES ? "Текстовый файл не должен быть больше 100 КБ." : null;
  }
  if (IMAGE_EXTENSIONS.includes(extension)) {
    return file.size > MAX_IMAGE_BYTES ? "Изображение слишком большое (максимум 5 МБ)." : null;
  }
  return "Допустимые форматы: JPG, GIF, PNG или TXT.";
}

// A text file has no signature, so its content is judged: valid UTF-8 without control
// characters. A renamed binary fails this; the server repeats the check.
const CONTROL_CHARACTERS = /[\u0000-\u0008\u000B\u000E-\u001F\u007F]/;

export async function checkTextFile(file) {
  const buffer = await file.arrayBuffer();
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch {
    return "Файл должен быть текстом в кодировке UTF-8.";
  }
  if (!text.trim()) return "Файл пуст.";
  return CONTROL_CHARACTERS.test(text) ? "Файл содержит двоичные данные, а не текст." : null;
}
