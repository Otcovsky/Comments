import { Fragment } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, MessageCircle, Paperclip } from "lucide-react";
import { excerpt, formatDate } from "../format.js";
import { Avatar } from "./CommentCard.jsx";
import Thread from "./Thread.jsx";
import { cx, focusRing } from "./ui.jsx";

const SORT_FIELDS = [
  { field: "username", label: "User Name", short: "Имя" },
  { field: "email", label: "E-mail", short: "E-mail" },
  { field: "created_at", label: "Дата", short: "Дата" },
];

function sortState(field, ordering) {
  const active = ordering.replace(/^-/, "") === field;
  const descending = ordering.startsWith("-");
  return {
    active,
    descending,
    Icon: active ? (descending ? ArrowDown : ArrowUp) : ArrowUpDown,
  };
}

function SortHeader({ field, label, ordering, onSort, className }) {
  const { active, descending, Icon } = sortState(field, ordering);
  return (
    <th
      scope="col"
      aria-sort={active ? (descending ? "descending" : "ascending") : "none"}
      className={cx("px-4 py-2.5 text-left font-medium", className)}
    >
      <button
        type="button"
        onClick={() => onSort(field)}
        className={cx(
          "group -mx-2 inline-flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 transition hover:bg-surface hover:text-fg",
          focusRing,
          active && "text-accent-ink",
        )}
      >
        {label}
        <Icon
          className={cx("size-3.5 transition-opacity", !active && "opacity-40 group-hover:opacity-80")}
          aria-hidden="true"
        />
      </button>
    </th>
  );
}

// Below md the table collapses into a list without a header, so sorting moves here.
function CompactSort({ ordering, onSort }) {
  return (
    <div className="flex items-center gap-2 overflow-x-auto border-b border-line px-4 py-2.5 md:hidden">
      <span className="shrink-0 text-xs text-muted">Сортировка</span>
      {SORT_FIELDS.map(({ field, short }) => {
        const { active, descending, Icon } = sortState(field, ordering);
        return (
          <button
            key={field}
            type="button"
            onClick={() => onSort(field)}
            aria-pressed={active}
            aria-label={active ? `${short}, ${descending ? "по убыванию" : "по возрастанию"}` : short}
            className={cx(
              "inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition",
              focusRing,
              active
                ? "border-accent/30 bg-accent-soft text-accent-ink"
                : "border-line text-muted hover:text-fg",
            )}
          >
            {short}
            <Icon className={cx("size-3", !active && "opacity-50")} aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}

function AttachmentMark() {
  return (
    <span className="ml-1.5 inline-flex align-[-2px] text-muted" title="Есть вложение">
      <Paperclip className="size-3.5" aria-hidden="true" />
      <span className="sr-only">(есть вложение)</span>
    </span>
  );
}

export function TableSkeleton() {
  return (
    <div className="divide-y divide-line" aria-busy="true" aria-label="Загрузка">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} className="flex items-center gap-3 px-4 py-4">
          <div className="skeleton size-9 shrink-0 rounded-full" />
          <div className="flex-1 space-y-2">
            <div className="skeleton h-3 w-32" />
            <div className="skeleton h-3 w-3/4 max-w-md" />
          </div>
          <div className="skeleton h-5 w-10 rounded-full" />
        </div>
      ))}
    </div>
  );
}

export default function CommentsTable({ comments, ordering, onSort, thread }) {
  return (
    <div className="animate-fade-in">
      <CompactSort ordering={ordering} onSort={onSort} />
      <table className="w-full text-sm">
        <thead className="hidden border-b border-line bg-subtle/60 text-xs text-muted md:table-header-group">
          <tr>
            <SortHeader field="username" label="User Name" ordering={ordering} onSort={onSort} />
            <SortHeader field="email" label="E-mail" ordering={ordering} onSort={onSort} />
            <SortHeader field="created_at" label="Дата" ordering={ordering} onSort={onSort} />
            <th scope="col" className="px-4 py-2.5 text-left font-medium">
              Сообщение
            </th>
            <th scope="col" className="px-4 py-2.5 text-right font-medium">
              Ответы
            </th>
          </tr>
        </thead>
        <tbody>
          {comments.map((comment) => {
            const expanded = thread.rootId === comment.id;
            const text = excerpt(comment.text);
            return (
              <Fragment key={comment.id}>
                <tr
                  className={cx(
                    "group cursor-pointer border-b border-line transition-colors outline-none",
                    "hover:bg-subtle/70 focus-visible:bg-subtle",
                    expanded && "bg-subtle/70",
                  )}
                  onClick={() => thread.toggleRoot(comment.id)}
                  tabIndex={0}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter" && event.key !== " ") return;
                    event.preventDefault(); // Space would otherwise scroll the page.
                    thread.toggleRoot(comment.id);
                  }}
                  aria-expanded={expanded}
                >
                  <td
                    className={cx(
                      "py-3.5 pr-3 pl-4 transition-shadow",
                      expanded
                        ? "shadow-[inset_3px_0_0_var(--color-accent)]"
                        : "group-focus-visible:shadow-[inset_3px_0_0_var(--color-accent)]",
                    )}
                  >
                    <div className="flex items-center gap-3">
                      <Avatar name={comment.username} />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-baseline gap-x-2">
                          <span className="min-w-0 truncate font-medium md:whitespace-nowrap">{comment.username}</span>
                          <span className="shrink-0 text-xs text-muted tabular-nums md:hidden">
                            {formatDate(comment.createdAt)}
                          </span>
                        </div>
                        <p className="mt-0.5 line-clamp-2 text-muted md:hidden">
                          {text}
                          {comment.attachment && <AttachmentMark />}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td className="hidden px-4 py-3.5 text-muted md:table-cell">
                    <div className="max-w-52 truncate" title={comment.email}>
                      {comment.email}
                    </div>
                  </td>
                  <td className="hidden px-4 py-3.5 whitespace-nowrap text-muted tabular-nums md:table-cell">
                    {formatDate(comment.createdAt)}
                  </td>
                  <td className="hidden min-w-56 px-4 py-3.5 md:table-cell">
                    <p className="line-clamp-2 text-fg/85">
                      {text}
                      {comment.attachment && <AttachmentMark />}
                    </p>
                  </td>
                  <td className="py-3.5 pr-4 pl-2 text-right">
                    <div className="inline-flex items-center gap-2">
                      <span
                        className={cx(
                          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium tabular-nums",
                          comment.replyCount ? "bg-accent-soft text-accent-ink" : "bg-subtle text-muted",
                        )}
                        title="Ответы"
                      >
                        <MessageCircle className="size-3.5" aria-hidden="true" />
                        <span className="sr-only">Ответов:</span>
                        {comment.replyCount}
                      </span>
                      <ChevronDown
                        className={cx(
                          "size-4 text-muted transition-transform duration-200 group-hover:text-fg",
                          expanded && "rotate-180 text-accent-ink",
                        )}
                        aria-hidden="true"
                      />
                    </div>
                  </td>
                </tr>
                {expanded && (
                  <tr>
                    <td colSpan={5} className="border-b border-line bg-subtle/50 px-3 py-4 sm:px-6 sm:py-5">
                      {/* Keeps long thread content from widening the table past the screen. */}
                      <div className="contain-inline-size">
                        <Thread root={comment} thread={thread} />
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
