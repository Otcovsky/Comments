import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { FileText, ImageIcon, X } from "lucide-react";
import { cx, focusRing } from "./ui.jsx";

const LightboxContext = createContext(() => {});
const CLOSE_ANIMATION_MS = 200;

export function useLightbox() {
  return useContext(LightboxContext);
}

// item: { type: "image" | "text", src, title }
export function LightboxProvider({ children }) {
  const [item, setItem] = useState(null);
  const [closing, setClosing] = useState(false);
  const [text, setText] = useState("");
  const closeTimer = useRef(null);
  const closeButton = useRef(null);

  const open = useCallback((next) => {
    // A pending close from the previous item must not hide the new one.
    clearTimeout(closeTimer.current);
    setClosing(false);
    setText("");
    setItem(next);
  }, []);

  const close = useCallback(() => {
    setClosing(true);
    clearTimeout(closeTimer.current);
    closeTimer.current = setTimeout(() => setItem(null), CLOSE_ANIMATION_MS);
  }, []);

  useEffect(() => () => clearTimeout(closeTimer.current), []);

  useEffect(() => {
    if (item?.type !== "text") return;
    let cancelled = false;
    fetch(item.src)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.text();
      })
      .then((content) => !cancelled && setText(content))
      .catch(() => !cancelled && setText("Не удалось загрузить файл."));
    return () => {
      cancelled = true;
    };
  }, [item]);

  const isOpen = item !== null;

  useEffect(() => {
    if (!isOpen) return;
    // Modal behaviour: focus moves into the dialog and stays there, the page
    // underneath does not scroll, and focus returns to the opener on close.
    const opener = document.activeElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButton.current?.focus();

    const onKey = (event) => {
      if (event.key === "Escape") close();
      if (event.key === "Tab") {
        // The close button is the only focusable element inside.
        event.preventDefault();
        closeButton.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, [isOpen, close]);

  const CaptionIcon = item?.type === "image" ? ImageIcon : FileText;

  return (
    <LightboxContext.Provider value={open}>
      {children}
      {item && (
        <div
          className={cx(
            "fixed inset-0 z-50 flex items-center justify-center bg-zinc-950/80 p-4 backdrop-blur-sm sm:p-8",
            closing ? "animate-fade-out" : "animate-fade-in",
          )}
          onClick={close}
          role="dialog"
          aria-modal="true"
          aria-label={item.title}
        >
          <div
            className={cx(
              "relative max-h-full max-w-[min(56rem,100%)] rounded-2xl bg-surface p-3 shadow-2xl ring-1 ring-white/10",
              closing ? "animate-zoom-out" : "animate-zoom-in",
            )}
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              ref={closeButton}
              className={cx(
                "absolute -top-3 -right-3 grid size-9 cursor-pointer place-items-center rounded-full bg-surface text-fg shadow-lg ring-1 ring-line transition hover:scale-105",
                focusRing,
              )}
              onClick={close}
              aria-label="Закрыть"
            >
              <X className="size-4" aria-hidden="true" />
            </button>
            {item.type === "image" ? (
              <img src={item.src} alt={item.title} className="mx-auto block max-h-[78vh] max-w-full rounded-lg sm:min-w-80" />
            ) : (
              <pre className="max-h-[75vh] min-w-[min(36rem,80vw)] overflow-auto rounded-lg bg-subtle p-4 font-mono text-sm leading-relaxed break-words whitespace-pre-wrap">
                {text || "Загрузка…"}
              </pre>
            )}
            {item.title && (
              <div className="mt-2.5 flex items-center justify-center gap-1.5 text-xs text-muted">
                <CaptionIcon className="size-3.5" aria-hidden="true" />
                {item.title}
              </div>
            )}
          </div>
        </div>
      )}
    </LightboxContext.Provider>
  );
}
