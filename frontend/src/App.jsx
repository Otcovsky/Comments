import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MessageSquareDashed, MessagesSquare, PenLine, RotateCw, TriangleAlert, X } from "lucide-react";
import { api, ApiError, orderingEnum } from "./api.js";
import CommentForm from "./components/CommentForm.jsx";
import CommentsTable, { TableSkeleton } from "./components/CommentsTable.jsx";
import Pagination, { PageSizePicker } from "./components/Pagination.jsx";
import { Button, cx, EmptyState } from "./components/ui.jsx";
import { useCommentsSocket } from "./socket.js";
import { useThread } from "./thread.js";

const PAGE_SIZES = [10, 25, 50]; // The sizes offered here; the server's limit is in docs/api.md.
const DEFAULT_PAGE_SIZE = 25;
const PAGE_SIZE_KEY = "comments.pageSize";
const DEFAULT_ORDERING = "-created_at"; // LIFO
// Everyone on a page hears about a new comment at once: a random delay spreads their
// reloads out, and the comments that arrive during it share one reload.
const LIVE_RELOAD_MAX_DELAY_MS = 1000;

function loadPageSize() {
  try {
    const saved = JSON.parse(localStorage.getItem(PAGE_SIZE_KEY));
    return PAGE_SIZES.includes(saved) ? saved : DEFAULT_PAGE_SIZE;
  } catch {
    return DEFAULT_PAGE_SIZE;
  }
}

function savePageSize(size) {
  try {
    localStorage.setItem(PAGE_SIZE_KEY, JSON.stringify(size));
  } catch {
    // Storage may be unavailable (private mode); the size just resets on reload.
  }
}

function plural(count, [one, few, many]) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

// A live update can push the open comment off this page; its row stays until the next
// reload so the thread, and any reply being typed in it, do not vanish.
function keepRow(previous, result, id) {
  if (id === null || result.results.some((comment) => comment.id === id)) return result;
  const row = previous.results.find((comment) => comment.id === id);
  return row ? { ...result, results: [...result.results, row] } : result;
}

function LiveStatus({ connected }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
        connected ? "border-success/30 text-success" : "border-line text-muted",
      )}
      title="Обновления в реальном времени"
    >
      <span className="relative flex size-2">
        {connected && <span className="absolute inset-0 animate-ping rounded-full bg-success opacity-60" />}
        <span className={cx("relative size-2 rounded-full", connected ? "bg-success" : "bg-muted/50")} />
      </span>
      {connected ? "Онлайн" : "Офлайн"}
    </span>
  );
}

export default function App() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(loadPageSize);
  const [ordering, setOrdering] = useState(DEFAULT_ORDERING);
  const [data, setData] = useState({ count: 0, results: [] });
  const [status, setStatus] = useState("loading");
  const thread = useThread();
  const [formOpen, setFormOpen] = useState(false);
  // A reload of the rows on screen failed; they are kept, but may be out of date.
  const [stale, setStale] = useState(false);
  const requestId = useRef(0);
  const liveReload = useRef(null);
  // The page, its size and the ordering of the rows on screen.
  const shownQuery = useRef(null);
  const expandedRef = useRef(thread.rootId);
  expandedRef.current = thread.rootId;

  const load = useCallback(() => {
    // This request reads everything committed so far, so a waiting live reload adds nothing.
    clearTimeout(liveReload.current);
    liveReload.current = null;
    // Only the latest request may update the table (fast sort clicks, live updates).
    const current = ++requestId.current;
    const query = `${page} ${pageSize} ${ordering}`;
    api
      .listComments({ page, pageSize, ordering })
      .then((result) => {
        if (current !== requestId.current) return;
        const same = shownQuery.current === query;
        shownQuery.current = query;
        setData((previous) => (same ? keepRow(previous, result, expandedRef.current) : result));
        setStatus("ready");
        setStale(false);
      })
      .catch((error) => {
        if (current !== requestId.current) return;
        // The page no longer exists (comments were deleted): start over from page 1.
        if (error instanceof ApiError && error.code === "PAGE_NOT_FOUND" && page > 1) setPage(1);
        // The rows on screen still match the page and ordering, so they stay with an open
        // thread and its reply form; other rows would not match, so an error replaces them.
        else if (shownQuery.current === query) setStale(true);
        else setStatus("error");
      });
  }, [page, pageSize, ordering]);

  useEffect(load, [load]);

  const reloadSoon = () => {
    if (liveReload.current !== null) return;
    liveReload.current = setTimeout(load, Math.random() * LIVE_RELOAD_MAX_DELAY_MS);
  };

  const viewport = useMemo(
    () => ({
      type: "viewport",
      page,
      pageSize,
      ordering: orderingEnum(ordering),
      thread: thread.rootId,
      branches: thread.branches,
    }),
    [page, pageSize, ordering, thread.rootId, thread.branches],
  );

  const connected = useCommentsSocket(
    viewport,
    (event) => {
      if (event.type === "comment.created") {
        if (event.parentId === null) reloadSoon();
        else thread.reloadBranch(event.parentId);
      }
      if (event.type === "comment.replyCount") {
        const { id, replyCount } = event;
        setData((previous) => ({
          ...previous,
          results: previous.results.map((comment) => (comment.id === id ? { ...comment, replyCount } : comment)),
        }));
        thread.setReplyCount(id, replyCount);
      }
    },
    () => {
      // Catch up on comments posted while offline; open branches reload too.
      thread.reloadAll();
      load();
    },
  );

  const sort = (field) => {
    setOrdering((current) => {
      if (current.replace(/^-/, "") === field) return current.startsWith("-") ? field : `-${field}`;
      return field === "created_at" ? DEFAULT_ORDERING : field;
    });
    setPage(1);
    thread.close();
  };

  const changePage = (next) => {
    setPage(next);
    thread.close();
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const changePageSize = (next) => {
    setPageSize(next);
    savePageSize(next);
    setPage(1);
    thread.close();
  };

  const openForm = () => {
    setFormOpen(true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const onCreated = () => {
    setFormOpen(false);
    // Show the new comment: it is first on page 1 with the default ordering.
    if (page === 1 && ordering === DEFAULT_ORDERING) load();
    else {
      setPage(1);
      setOrdering(DEFAULT_ORDERING);
    }
  };

  const totalPages = Math.max(1, Math.ceil(data.count / pageSize));

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-line/70 bg-canvas/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent text-white shadow-md shadow-accent/30">
              <MessagesSquare className="size-5" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <h1 className="truncate text-lg font-semibold leading-tight tracking-tight">Комментарии</h1>
              <p className="text-sm text-muted tabular-nums">
                {data.count} {plural(data.count, ["обсуждение", "обсуждения", "обсуждений"])}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 sm:gap-3">
            <LiveStatus connected={connected} />
            <Button variant="primary" onClick={() => setFormOpen((open) => !open)} aria-expanded={formOpen}>
              {formOpen ? <X className="size-4" aria-hidden="true" /> : <PenLine className="size-4" aria-hidden="true" />}
              <span className="sr-only sm:not-sr-only">{formOpen ? "Скрыть форму" : "Написать"}</span>
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:py-8">
        {formOpen && (
          <section className="animate-slide-down rounded-2xl border border-line bg-surface p-4 shadow-sm sm:p-6">
            <div className="mb-5 flex items-center gap-3">
              <span className="grid size-9 place-items-center rounded-lg bg-accent-soft text-accent-ink">
                <PenLine className="size-4" aria-hidden="true" />
              </span>
              <div>
                <h2 className="font-semibold leading-tight">Новый комментарий</h2>
                <p className="text-sm text-muted">Поля со звёздочкой обязательны</p>
              </div>
            </div>
            <CommentForm onSuccess={onCreated} onCancel={() => setFormOpen(false)} />
          </section>
        )}

        <section className="overflow-hidden rounded-2xl border border-line bg-surface shadow-sm">
          {status === "error" && (
            <EmptyState
              icon={TriangleAlert}
              tone="danger"
              title="Не удалось загрузить комментарии"
              text="Проверьте соединение и попробуйте ещё раз."
              action={
                <Button onClick={load}>
                  <RotateCw className="size-4" aria-hidden="true" />
                  Повторить
                </Button>
              }
            />
          )}
          {status === "loading" && <TableSkeleton />}
          {status === "ready" && stale && (
            <div
              className="flex items-center gap-2 border-b border-danger/20 bg-danger-soft px-4 py-2 text-sm text-danger animate-fade-in"
              role="status"
            >
              <TriangleAlert className="size-4 shrink-0" aria-hidden="true" />
              <span className="min-w-0 flex-1">Не удалось обновить список, он может быть устаревшим.</span>
              <Button size="sm" variant="ghost" onClick={load}>
                <RotateCw className="size-3.5" aria-hidden="true" />
                Повторить
              </Button>
            </div>
          )}
          {status === "ready" && data.results.length === 0 && (
            <EmptyState
              icon={MessageSquareDashed}
              title="Пока нет комментариев"
              text="Начните первое обсуждение — оно появится здесь у всех сразу."
              action={
                !formOpen && (
                  <Button variant="primary" onClick={openForm}>
                    <PenLine className="size-4" aria-hidden="true" />
                    Написать комментарий
                  </Button>
                )
              }
            />
          )}
          {/* After a failed reload the old rows would no longer match the page or ordering. */}
          {status === "ready" && data.results.length > 0 && (
            <CommentsTable
              comments={data.results}
              ordering={ordering}
              onSort={sort}
              thread={thread}
            />
          )}
        </section>

        <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-3">
          <Pagination page={page} totalPages={totalPages} onChange={changePage} />
          {/* Stays when everything fits on one page, so a larger size can be undone. */}
          {data.count > PAGE_SIZES[0] && (
            <PageSizePicker sizes={PAGE_SIZES} value={pageSize} onChange={changePageSize} />
          )}
        </div>
      </main>
    </div>
  );
}
