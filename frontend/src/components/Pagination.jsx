import { ChevronLeft, ChevronRight, Ellipsis } from "lucide-react";
import { cx, focusRing } from "./ui.jsx";

function pageNumbers(current, total) {
  const pages = new Set([1, total, current - 1, current, current + 1]);
  const sorted = [...pages].filter((page) => page >= 1 && page <= total).sort((a, b) => a - b);
  const result = [];
  sorted.forEach((page, index) => {
    if (index > 0 && page - sorted[index - 1] > 1) result.push("…");
    result.push(page);
  });
  return result;
}

const pageButton = cx(
  "grid h-9 min-w-9 cursor-pointer place-items-center rounded-lg px-2 text-sm font-medium tabular-nums transition",
  "disabled:pointer-events-none disabled:opacity-40",
  focusRing,
);

export default function Pagination({ page, totalPages, onChange }) {
  if (totalPages <= 1) return null;
  return (
    <nav className="flex flex-wrap items-center justify-center gap-1" aria-label="Страницы">
      <button
        type="button"
        className={cx(pageButton, "text-muted hover:bg-surface hover:text-fg hover:shadow-xs")}
        disabled={page === 1}
        onClick={() => onChange(page - 1)}
        aria-label="Предыдущая страница"
      >
        <ChevronLeft className="size-4" aria-hidden="true" />
      </button>
      {pageNumbers(page, totalPages).map((item, index) =>
        item === "…" ? (
          <span key={`gap-${index}`} className="grid size-9 place-items-center text-muted" aria-hidden="true">
            <Ellipsis className="size-4" />
          </span>
        ) : (
          <button
            type="button"
            key={item}
            className={cx(
              pageButton,
              item === page
                ? "bg-accent text-white shadow-sm shadow-accent/25"
                : "text-muted hover:bg-surface hover:text-fg hover:shadow-xs",
            )}
            aria-current={item === page ? "page" : undefined}
            onClick={() => onChange(item)}
          >
            {item}
          </button>
        ),
      )}
      <button
        type="button"
        className={cx(pageButton, "text-muted hover:bg-surface hover:text-fg hover:shadow-xs")}
        disabled={page === totalPages}
        onClick={() => onChange(page + 1)}
        aria-label="Следующая страница"
      >
        <ChevronRight className="size-4" aria-hidden="true" />
      </button>
    </nav>
  );
}
