import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { Button } from "./ui.jsx";

const FOCUSABLE =
  "a[href], button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex='-1'])";

// A dialog over the page. Pass onClose={null} while it must not be closed.
export default function Modal({ title, icon: Icon, onClose, children }) {
  const titleId = useId();
  const panel = useRef(null);
  const closeRef = useRef(onClose);
  const pressedBackdrop = useRef(false);
  // Read during the first render: by the time effects run, a child with
  // autoFocus has already moved focus away from the opener.
  const [opener] = useState(() => document.activeElement);
  closeRef.current = onClose;

  useEffect(() => {
    // Modal behaviour: focus moves into the dialog and stays there, the page
    // underneath does not scroll, and focus returns to the opener on close.
    const node = panel.current;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // A child with autoFocus has already taken focus by now.
    if (!node.contains(document.activeElement)) node.focus();

    const onKey = (event) => {
      if (event.key === "Escape") closeRef.current?.();
      if (event.key !== "Tab") return;
      const items = [...node.querySelectorAll(FOCUSABLE)];
      const index = items.indexOf(document.activeElement);
      if (index === -1 || index === (event.shiftKey ? 0 : items.length - 1)) {
        event.preventDefault();
        items.at(event.shiftKey ? -1 : 0)?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      // Deferred, and only once the dialog is really gone: StrictMode re-runs
      // this effect on a dialog that stays open and keeps its focus.
      setTimeout(() => {
        if (!node.isConnected && opener instanceof HTMLElement && opener.isConnected) opener.focus();
      });
    };
  }, [opener]);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex overflow-y-auto bg-zinc-950/60 p-4 backdrop-blur-sm animate-fade-in sm:p-8"
      // Closes only on a click that started on the backdrop, not on a text
      // selection dragged out of the dialog.
      onMouseDown={(event) => (pressedBackdrop.current = event.target === event.currentTarget)}
      onClick={(event) => pressedBackdrop.current && event.target === event.currentTarget && onClose?.()}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="m-auto w-full max-w-xl rounded-2xl bg-surface shadow-2xl ring-1 ring-line outline-none animate-zoom-in"
      >
        <header className="flex items-center gap-3 border-b border-line px-5 py-4">
          {Icon && (
            <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent-ink">
              <Icon className="size-4" aria-hidden="true" />
            </span>
          )}
          <h2 id={titleId} className="min-w-0 flex-1 font-semibold leading-tight">
            {title}
          </h2>
          <Button variant="ghost" size="icon" onClick={onClose} disabled={!onClose} aria-label="Закрыть" className="size-9">
            <X className="size-4" aria-hidden="true" />
          </Button>
        </header>
        {children}
      </div>
    </div>,
    document.body,
  );
}
