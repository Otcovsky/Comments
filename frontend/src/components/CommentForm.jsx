import { useEffect, useId, useRef, useState } from "react";
import {
  Bold,
  CircleAlert,
  Code,
  Eye,
  FileImage,
  FileText,
  Globe,
  Italic,
  Link,
  LoaderCircle,
  Mail,
  Paperclip,
  PenLine,
  RefreshCw,
  Send,
  ShieldCheck,
  User,
  X,
} from "lucide-react";
import { api, ApiError } from "../api.js";
import { formatSize } from "../format.js";
import { ImageCheckError, prepareImage } from "../image.js";
import { checkTags, checkTextFile, isTextFile, validateCaptcha, validateComment, validateFile } from "../validation.js";
import CommentCard from "./CommentCard.jsx";
import Modal from "./Modal.jsx";
import { Button, cx, focusRing } from "./ui.jsx";

const PROFILE_KEY = "comments.profile";
const TOKEN_KEY = "comments.token";
const MAX_TEXT_LENGTH = 5000;
const CAPTCHA_LOAD_ERROR = "Не удалось загрузить капчу.";
// The server keeps a captcha for 5 minutes; an older picture is replaced, not reused.
const CAPTCHA_REUSE_MS = 4 * 60 * 1000;
const TOOLBAR = [
  { tag: "strong", icon: Bold, label: "Жирный" },
  { tag: "i", icon: Italic, label: "Курсив" },
  { tag: "code", icon: Code, label: "Код" },
  { tag: "a", icon: Link, label: "Ссылка" },
];
const SERVER_FIELDS = {
  username: "username",
  email: "email",
  homePage: "homePage",
  text: "text",
  captchaKey: "captcha",
  captchaValue: "captcha",
  attachment: "file",
};
// Errors in these fields are fixed in the form, so the dialog closes to show them.
const FORM_FIELDS = ["username", "email", "homePage", "text", "file"];

const inputClass = cx(
  "h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm text-fg shadow-xs transition outline-none",
  "placeholder:text-muted/70 focus:border-accent focus:ring-4 focus:ring-accent/15",
  "aria-invalid:border-danger aria-invalid:focus:ring-danger/15",
);

function loadProfile() {
  try {
    return JSON.parse(localStorage.getItem(PROFILE_KEY)) || {};
  } catch {
    return {};
  }
}

function saveProfile(profile) {
  try {
    localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
  } catch {
    // Storage may be unavailable (private mode); the form works without it.
  }
}

// The token of a solved captcha, if it is still valid for this author. The server has the
// last word: a token it turns down brings the captcha back (see submit).
function loadToken(username, email) {
  try {
    const saved = JSON.parse(localStorage.getItem(TOKEN_KEY));
    const valid = saved?.username === username && saved.email === email && Date.now() < saved.expiresAt;
    return valid ? saved.token : null;
  } catch {
    return null;
  }
}

function saveToken(saved) {
  try {
    localStorage.setItem(TOKEN_KEY, JSON.stringify(saved));
  } catch {
    // Without storage the author just solves a captcha every time.
  }
}

function mapServerErrors(fields) {
  const errors = {};
  for (const [key, message] of Object.entries(fields)) {
    errors[SERVER_FIELDS[key] || "form"] = message;
  }
  return Object.keys(errors).length ? errors : { form: "Проверьте введённые данные." };
}

function Field({ id, label, required, hint, error, className, children }) {
  return (
    <div className={cx("flex min-w-0 flex-col gap-1.5", className)}>
      <label className="text-sm font-medium" htmlFor={id}>
        {label}
        {required && <span className="text-danger"> *</span>}
        {hint && <span className="ml-1.5 font-normal text-muted">{hint}</span>}
      </label>
      {children}
      {error && (
        <span className="flex items-start gap-1 text-xs text-danger animate-fade-in" id={`${id}-error`}>
          <CircleAlert className="mt-px size-3.5 shrink-0" aria-hidden="true" />
          {error}
        </span>
      )}
    </div>
  );
}

function IconInput({ icon: Icon, className, ...props }) {
  return (
    <div className={cx("relative min-w-0", className)}>
      <Icon
        className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted"
        aria-hidden="true"
      />
      <input className={cx(inputClass, "pl-9")} {...props} />
    </div>
  );
}

function FormAlert({ children }) {
  return (
    <div
      className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger-soft px-3 py-2.5 text-sm text-danger animate-fade-in"
      role="alert"
    >
      <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      {children}
    </div>
  );
}

const fileIcon = (file) => (isTextFile(file) ? FileText : FileImage);
const FILE_BUSY_ERROR = "Файл ещё обрабатывается, подождите секунду.";

function useObjectUrl(blob) {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    if (!blob) {
      setUrl(null);
      return undefined;
    }
    const next = URL.createObjectURL(blob);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [blob]);
  return url;
}

function imageSummary(file, check) {
  if (check.busy) return "Проверка файла…";
  const size = check.resized ? `${formatSize(check.originalSize)} → ${formatSize(file.size)}` : formatSize(file.size);
  const dimensions = `${check.width}×${check.height}`;
  return `${size} · ${check.shrinksOnServer ? `уменьшится до ${dimensions}` : dimensions}`;
}

export default function CommentForm({ parentId = null, onSuccess, onCancel }) {
  const id = useId();
  const [values, setValues] = useState(() => {
    const profile = loadProfile();
    return {
      username: profile.username || "",
      email: profile.email || "",
      homePage: profile.homePage || "",
      text: "",
      captcha: "",
    };
  });
  const [file, setFile] = useState(null);
  // Images only: { busy } while checked in the browser, then { width, height, resized, ... }.
  const [fileCheck, setFileCheck] = useState(null);
  const [captcha, setCaptcha] = useState(null);
  // Set while the dialog sends the comment with a token instead of a captcha.
  const [token, setToken] = useState(null);
  const [errors, setErrors] = useState({});
  const [preview, setPreview] = useState(null);
  // null, "preview" (the message only) or "captcha" (the message and the code to send it).
  const [dialog, setDialog] = useState(null);
  const [opening, setOpening] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const textRef = useRef(null);
  const fileRef = useRef(null);
  const captchaRequest = useRef(0);
  const previewRequest = useRef(0);
  const fileRequest = useRef(0);
  const thumbnailUrl = useObjectUrl(fileCheck && !fileCheck.busy ? file : null);

  const refreshCaptcha = () => {
    // Only the latest response may be shown: responses can arrive out of order,
    // and the key sent with the form must match the picture the user sees.
    const current = ++captchaRequest.current;
    setCaptcha(null);
    setErrors((errors) => (errors.captcha === CAPTCHA_LOAD_ERROR ? { ...errors, captcha: undefined } : errors));
    api
      .getCaptcha()
      .then((data) => current === captchaRequest.current && setCaptcha({ ...data, issuedAt: Date.now() }))
      .catch(
        () =>
          current === captchaRequest.current &&
          setErrors((errors) => ({ ...errors, captcha: CAPTCHA_LOAD_ERROR })),
      );
  };

  const discardCaptcha = () => {
    captchaRequest.current += 1;
    setCaptcha(null);
  };

  // Links an input to its error message for screen readers (see Field).
  const describe = (name, fieldId) =>
    errors[name] ? { "aria-invalid": true, "aria-describedby": `${fieldId}-error` } : {};

  const resetPreview = () => {
    // Also discards a preview request still in flight for the old text.
    previewRequest.current += 1;
    setPreview(null);
  };

  const setField = (name) => (event) => {
    const { value } = event.target;
    setValues((current) => ({ ...current, [name]: value }));
    setErrors((current) => ({ ...current, [name]: undefined, form: undefined }));
    if (name === "text") resetPreview();
  };

  const insertTag = (tag) => {
    const area = textRef.current;
    const { selectionStart: start, selectionEnd: end, value } = area;
    let open = `<${tag}>`;
    if (tag === "a") {
      const url = window.prompt("Адрес ссылки", "https://");
      if (!url) return;
      open = `<a href="${url.trim().replace(/"/g, "%22")}">`;
    }
    const text = `${value.slice(0, start)}${open}${value.slice(start, end)}</${tag}>${value.slice(end)}`;
    setValues((current) => ({ ...current, text }));
    resetPreview();
    requestAnimationFrame(() => {
      area.focus();
      area.setSelectionRange(start + open.length, end + open.length);
    });
  };

  // Resolves to the rendered HTML, or null if it failed or the text changed meanwhile.
  const loadPreview = async () => {
    const current = ++previewRequest.current;
    try {
      const html = await api.previewText(values.text);
      return current === previewRequest.current ? html : null;
    } catch (requestError) {
      if (current !== previewRequest.current) return null;
      const message = requestError instanceof ApiError ? requestError.fields.text : null;
      setErrors((current) => ({ ...current, text: message || "Не удалось построить предпросмотр." }));
      return null;
    }
  };

  const openDialog = async (step) => {
    if (opening) return;
    let clientErrors;
    if (step === "captcha") {
      clientErrors = validateComment(values);
      // errors.file also holds what the content checks found when the file was picked.
      const fileError = file && (validateFile(file) || (fileCheck?.busy && FILE_BUSY_ERROR) || errors.file);
      if (fileError) clientErrors.file = fileError;
    } else {
      const textError = values.text.trim() ? checkTags(values.text) : "Введите текст сообщения.";
      clientErrors = textError ? { text: textError } : {};
    }
    if (Object.keys(clientErrors).length) {
      setDialog(null);
      setErrors(clientErrors);
      return;
    }

    setErrors((current) => ({ file: current.file }));
    let html = preview;
    if (html === null) {
      setOpening(step);
      html = await loadPreview();
      setOpening(null);
      if (html === null) return;
      setPreview(html);
    }
    setDialog(step);
    const saved = step === "captcha" ? loadToken(values.username.trim(), values.email.trim()) : null;
    setToken(saved);
    // The captcha is issued only now, when the user is about to type it in.
    if (step === "captcha" && !saved && !(captcha && Date.now() - captcha.issuedAt < CAPTCHA_REUSE_MS)) {
      setValues((current) => ({ ...current, captcha: "" }));
      refreshCaptcha();
    }
  };

  const closeDialog = () => {
    setDialog(null);
    setErrors((current) => ({ ...current, captcha: undefined, form: undefined }));
  };

  const onFileChange = async (event) => {
    const selected = event.target.files[0] || null;
    const current = ++fileRequest.current;
    const fileError = selected ? validateFile(selected) : undefined;
    setFile(selected);
    setErrors((errors) => ({ ...errors, file: fileError }));
    if (!selected || fileError) {
      setFileCheck(null);
      return;
    }

    setFileCheck({ busy: true });
    try {
      if (isTextFile(selected)) {
        const textError = await checkTextFile(selected);
        if (current !== fileRequest.current) return;
        setFileCheck(null);
        setErrors((errors) => ({ ...errors, file: textError || undefined }));
        return;
      }
      const result = await prepareImage(selected);
      if (current !== fileRequest.current) return;
      setFile(result.file);
      setFileCheck({ ...result, originalSize: selected.size });
      setErrors((errors) => (errors.file === FILE_BUSY_ERROR ? { ...errors, file: undefined } : errors));
    } catch (checkError) {
      if (current !== fileRequest.current) return;
      // Anything but a definite verdict leaves the file to the server's checks.
      setFileCheck(null);
      setErrors((errors) => ({
        ...errors,
        file: checkError instanceof ImageCheckError ? checkError.message : undefined,
      }));
    }
  };

  const clearFile = () => {
    fileRequest.current += 1;
    setFile(null);
    setFileCheck(null);
    setErrors((current) => ({ ...current, file: undefined }));
    if (fileRef.current) fileRef.current.value = "";
  };

  const submit = async (event) => {
    event.preventDefault();
    if (!token) {
      const captchaError = captcha ? validateCaptcha(values.captcha) : "Капча ещё не загрузилась.";
      if (captchaError) {
        setErrors((current) => ({ ...current, captcha: captchaError }));
        return;
      }
    }

    const input = {
      username: values.username.trim(),
      email: values.email.trim(),
      homePage: values.homePage.trim(),
      text: values.text,
      parentId,
      captchaKey: token ? null : captcha.key,
      captchaValue: token ? null : values.captcha.trim(),
    };

    setSubmitting(true);
    try {
      const { comment, token: newToken, tokenExpiresIn } = await api.createComment(input, file, token);
      saveProfile({ username: values.username.trim(), email: values.email.trim(), homePage: values.homePage.trim() });
      if (newToken) {
        saveToken({
          token: newToken,
          username: values.username.trim(),
          email: values.email.trim(),
          expiresAt: Date.now() + tokenExpiresIn * 1000,
        });
      }
      setValues((current) => ({ ...current, text: "", captcha: "" }));
      clearFile();
      resetPreview();
      setErrors({});
      discardCaptcha();
      setDialog(null);
      onSuccess?.(comment);
    } catch (error) {
      const serverErrors =
        error instanceof ApiError && error.code === "VALIDATION"
          ? mapServerErrors(error.fields)
          : { form: "Не удалось отправить комментарий. Попробуйте ещё раз." };
      // The server asks for a captcha despite the token: it has expired or is no longer valid.
      const tokenRejected = token && serverErrors.captcha;
      if (tokenRejected) {
        saveToken(null);
        setToken(null);
      }
      // A captcha is single-use on the server, so every failed attempt needs a new one.
      setValues((current) => ({ ...current, captcha: "" }));
      if (FORM_FIELDS.some((name) => serverErrors[name])) {
        // Back to the form; the next attempt asks for a fresh captcha.
        discardCaptcha();
        setDialog(null);
        setErrors({ ...serverErrors, captcha: undefined });
      } else {
        setErrors(serverErrors);
        if (!token || tokenRejected) refreshCaptcha();
      }
    } finally {
      setSubmitting(false);
    }
  };

  const FileIcon = file && fileIcon(file);
  const captchaLoading = !captcha && errors.captcha !== CAPTCHA_LOAD_ERROR;
  const sendLabel = parentId ? "Ответить" : "Отправить";

  return (
    <>
      <form
        className="@container flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          openDialog("captcha");
        }}
        noValidate
      >
        <div className="grid gap-4 @xl:grid-cols-3">
          <Field id={`${id}-username`} label="User Name" required error={errors.username}>
            <IconInput
              icon={User}
              id={`${id}-username`}
              {...describe("username", `${id}-username`)}
              value={values.username}
              onChange={setField("username")}
              maxLength={64}
              autoComplete="nickname"
              placeholder="johndoe"
            />
          </Field>
          <Field id={`${id}-email`} label="E-mail" required error={errors.email}>
            <IconInput
              icon={Mail}
              id={`${id}-email`}
              {...describe("email", `${id}-email`)}
              type="email"
              value={values.email}
              onChange={setField("email")}
              maxLength={254}
              autoComplete="email"
              placeholder="you@example.com"
            />
          </Field>
          <Field id={`${id}-home`} label="Home page" error={errors.homePage}>
            <IconInput
              icon={Globe}
              id={`${id}-home`}
              {...describe("homePage", `${id}-home`)}
              type="url"
              value={values.homePage}
              onChange={setField("homePage")}
              placeholder="https://"
              maxLength={200}
              autoComplete="url"
            />
          </Field>
        </div>

        <Field id={`${id}-text`} label="Текст" required error={errors.text}>
          <div
            className={cx(
              "overflow-hidden rounded-lg border border-line bg-surface shadow-xs transition",
              "focus-within:border-accent focus-within:ring-4 focus-within:ring-accent/15",
              "has-[[aria-invalid=true]]:border-danger has-[[aria-invalid=true]]:focus-within:ring-danger/15",
            )}
          >
            <div
              className="flex items-center gap-0.5 border-b border-line bg-subtle/60 px-1.5 py-1"
              role="toolbar"
              aria-label="HTML-теги"
            >
              {TOOLBAR.map(({ tag, icon: Icon, label }) => (
                <button
                  type="button"
                  key={tag}
                  onClick={() => insertTag(tag)}
                  title={`${label} <${tag}>`}
                  aria-label={label}
                  className={cx(
                    "grid size-8 cursor-pointer place-items-center rounded-md text-muted transition hover:bg-surface hover:text-fg hover:shadow-xs",
                    focusRing,
                  )}
                >
                  <Icon className="size-4" aria-hidden="true" />
                </button>
              ))}
              <span
                className={cx(
                  "ml-auto pr-1.5 text-xs tabular-nums",
                  values.text.length > MAX_TEXT_LENGTH * 0.9 ? "text-danger" : "text-muted",
                )}
              >
                {values.text.length}/{MAX_TEXT_LENGTH}
              </span>
            </div>
            <textarea
              id={`${id}-text`}
              {...describe("text", `${id}-text`)}
              ref={textRef}
              rows={5}
              value={values.text}
              onChange={setField("text")}
              maxLength={MAX_TEXT_LENGTH}
              placeholder={'Разрешены теги: <a href="" title="">, <code>, <i>, <strong>'}
              className="block min-h-28 w-full resize-y bg-transparent px-3 py-2.5 text-sm leading-relaxed outline-none placeholder:text-muted/70"
            />
          </div>
        </Field>

        <Field id={`${id}-file`} label="Вложение" hint="JPG, GIF, PNG до 5 МБ · TXT до 100 КБ" error={errors.file}>
          <input
            id={`${id}-file`}
            {...describe("file", `${id}-file`)}
            ref={fileRef}
            type="file"
            accept=".jpg,.jpeg,.png,.gif,.txt"
            onChange={onFileChange}
            className="peer sr-only"
          />
          {file ? (
            <div
              className={cx(
                "flex h-14 min-w-0 items-center gap-3 rounded-lg border bg-surface px-3 text-sm shadow-xs",
                errors.file ? "border-danger" : "border-line",
              )}
            >
              {thumbnailUrl ? (
                <img
                  src={thumbnailUrl}
                  alt=""
                  className="size-10 shrink-0 rounded-md border border-line object-cover animate-fade-in"
                />
              ) : (
                <span className="grid size-8 shrink-0 place-items-center rounded-md bg-accent-soft text-accent-ink">
                  {fileCheck?.busy ? (
                    <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <FileIcon className="size-4" aria-hidden="true" />
                  )}
                </span>
              )}
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{file.name}</div>
                <div className="truncate text-xs text-muted" aria-live="polite">
                  {fileCheck ? imageSummary(file, fileCheck) : formatSize(file.size)}
                </div>
              </div>
              <Button variant="ghost" size="sm" onClick={clearFile} aria-label="Убрать файл" className="px-2">
                <X className="size-4" aria-hidden="true" />
              </Button>
            </div>
          ) : (
            <label
              htmlFor={`${id}-file`}
              className={cx(
                "flex h-14 cursor-pointer items-center justify-center gap-2 rounded-lg border border-dashed border-line bg-subtle/50 px-3 text-sm text-muted transition",
                "hover:border-accent/50 hover:bg-accent-soft/50 hover:text-accent-ink",
                "peer-focus-visible:border-accent peer-focus-visible:ring-4 peer-focus-visible:ring-accent/15",
              )}
            >
              <Paperclip className="size-4" aria-hidden="true" />
              Прикрепить файл
            </label>
          )}
        </Field>

        {errors.form && !dialog && <FormAlert>{errors.form}</FormAlert>}

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line pt-4">
          {onCancel && (
            <Button variant="ghost" onClick={onCancel}>
              Отмена
            </Button>
          )}
          <Button onClick={() => openDialog("preview")} aria-busy={opening === "preview"}>
            {opening === "preview" ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Eye className="size-4" aria-hidden="true" />
            )}
            Предпросмотр
          </Button>
          <Button type="submit" variant="primary" aria-busy={opening === "captcha"}>
            {opening === "captcha" ? (
              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Send className="size-4" aria-hidden="true" />
            )}
            {sendLabel}
          </Button>
        </div>
      </form>

      {dialog && (
        <Modal
          title={dialog === "captcha" ? "Подтвердите отправку" : "Предпросмотр"}
          icon={dialog === "captcha" ? ShieldCheck : Eye}
          onClose={submitting ? null : closeDialog}
        >
          <form onSubmit={submit} noValidate>
            <div className="space-y-4 p-5">
              <CommentCard
                comment={{
                  id: 0,
                  username: values.username.trim() || "Гость",
                  email: values.email.trim(),
                  homePage: "",
                  text: preview,
                  createdAt: new Date().toISOString(),
                  attachment: null,
                }}
              />
              {file && (
                <div className="flex min-w-0 items-center gap-2 text-sm text-muted">
                  {thumbnailUrl ? (
                    <img src={thumbnailUrl} alt="" className="size-8 shrink-0 rounded border border-line object-cover" />
                  ) : (
                    <FileIcon className="size-4 shrink-0" aria-hidden="true" />
                  )}
                  <span className="truncate">{file.name}</span>
                  <span className="shrink-0 text-xs">{formatSize(file.size)}</span>
                </div>
              )}

              {dialog === "captcha" && !token && (
                <Field
                  id={`${id}-captcha`}
                  label="Код с картинки"
                  required
                  error={errors.captcha}
                  className="border-t border-line pt-4"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="h-14 w-40 shrink-0 overflow-hidden rounded-lg border border-line bg-white">
                      {captcha ? (
                        <img src={captcha.image} alt="Код с картинки" width="160" height="56" className="block size-full" />
                      ) : (
                        <div className="skeleton size-full rounded-none" />
                      )}
                    </div>
                    <Button
                      size="icon"
                      onClick={refreshCaptcha}
                      disabled={submitting}
                      aria-label="Другой код"
                      title="Другой код"
                      className="size-14"
                    >
                      <RefreshCw className={cx("size-4", captchaLoading && "animate-spin")} aria-hidden="true" />
                    </Button>
                    <IconInput
                      icon={ShieldCheck}
                      className="min-w-28 flex-1"
                      id={`${id}-captcha`}
                      {...describe("captcha", `${id}-captcha`)}
                      value={values.captcha}
                      onChange={setField("captcha")}
                      maxLength={16}
                      autoComplete="off"
                      autoFocus
                      spellCheck={false}
                      placeholder="Код"
                    />
                  </div>
                </Field>
              )}

              {errors.form && <FormAlert>{errors.form}</FormAlert>}
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-4">
              <Button variant="ghost" onClick={closeDialog} disabled={submitting}>
                <PenLine className="size-4" aria-hidden="true" />
                Изменить
              </Button>
              {/* Distinct keys: React must not turn the clicked button into a submit button mid-click. */}
              {dialog === "preview" ? (
                <Button key="next" variant="primary" onClick={() => openDialog("captcha")}>
                  <Send className="size-4" aria-hidden="true" />
                  {sendLabel}
                </Button>
              ) : (
                <Button key="submit" type="submit" variant="primary" disabled={submitting}>
                  {submitting ? (
                    <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Send className="size-4" aria-hidden="true" />
                  )}
                  {submitting ? "Отправка…" : sendLabel}
                </Button>
              )}
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
