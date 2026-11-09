import { Clock, ExternalLink, FileText, Maximize2, Quote, Reply, X } from "lucide-react";
import { avatarColor, formatDate } from "../format.js";
import { useLightbox } from "./Lightbox.jsx";
import { Button, cx, focusRing } from "./ui.jsx";

export function Avatar({ name }) {
  return (
    <span
      className="grid size-9 shrink-0 place-items-center rounded-full text-sm font-semibold text-white shadow-sm ring-2 ring-surface"
      style={{ background: avatarColor(name) }}
      aria-hidden="true"
    >
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
}

function Attachment({ comment }) {
  const openLightbox = useLightbox();
  // Comments saved before names were kept have none.
  const name = comment.attachmentName;
  const title = name || `Вложение от ${comment.username}`;

  if (comment.attachmentType === "IMAGE") {
    const thumbnail = (
      <button
        type="button"
        title={name || undefined}
        className={cx(
          "group/thumb relative block cursor-zoom-in overflow-hidden rounded-xl border border-line bg-subtle",
          focusRing,
        )}
        onClick={() => openLightbox({ type: "image", src: comment.attachment, title })}
      >
        <img
          src={comment.attachment}
          alt={name ? `Вложение: ${name}` : "Вложение: изображение"}
          loading="lazy"
          className="block max-h-40 max-w-56 object-cover transition duration-300 group-hover/thumb:scale-105"
        />
        <span className="absolute inset-0 grid place-items-center bg-black/0 text-white opacity-0 transition group-hover/thumb:bg-black/30 group-hover/thumb:opacity-100">
          <Maximize2 className="size-5 drop-shadow" aria-hidden="true" />
        </span>
      </button>
    );
    if (!name) return thumbnail;
    return (
      <figure className="flex w-fit max-w-56 flex-col gap-1">
        {thumbnail}
        <figcaption className="truncate text-xs text-muted" aria-hidden="true">
          {name}
        </figcaption>
      </figure>
    );
  }
  return (
    <button
      type="button"
      title={name || undefined}
      className={cx(
        "inline-flex max-w-full cursor-pointer items-center gap-2 rounded-lg border border-line bg-subtle px-3 py-2 text-sm font-medium transition",
        "hover:border-accent/40 hover:bg-accent-soft hover:text-accent-ink",
        focusRing,
      )}
      onClick={() => openLightbox({ type: "text", src: comment.attachment, title })}
    >
      <FileText className="size-4 shrink-0" aria-hidden="true" />
      {name ? (
        <span className="min-w-0 truncate">{name}</span>
      ) : (
        <>
          Текстовый файл
          <span className="text-xs font-normal text-muted">.txt</span>
        </>
      )}
    </button>
  );
}

export default function CommentCard({ comment, quote, onReply, replying }) {
  const author = comment.homePage ? (
    <a
      href={comment.homePage}
      target="_blank"
      rel="noopener noreferrer nofollow"
      className="inline-flex items-center gap-1 rounded hover:text-accent-ink focus-visible:text-accent-ink focus-visible:outline-none"
    >
      {comment.username}
      <ExternalLink className="size-3 text-muted" aria-hidden="true" />
    </a>
  ) : (
    comment.username
  );

  return (
    <article className="rounded-xl border border-line bg-surface p-4 shadow-xs">
      <header className="flex items-center gap-3">
        <Avatar name={comment.username} />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
            <span className="font-semibold">{author}</span>
            <span className="hidden truncate text-xs text-muted sm:inline">{comment.email}</span>
          </div>
          <time
            className="flex items-center gap-1 text-xs text-muted tabular-nums"
            dateTime={comment.createdAt}
          >
            <Clock className="size-3" aria-hidden="true" />
            {formatDate(comment.createdAt)}
          </time>
        </div>
        {onReply && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onReply}
            className={replying ? "bg-subtle text-fg" : undefined}
          >
            {replying ? <X className="size-4" aria-hidden="true" /> : <Reply className="size-4" aria-hidden="true" />}
            <span className="sr-only sm:not-sr-only">{replying ? "Отмена" : "Ответить"}</span>
          </Button>
        )}
      </header>
      <div className="mt-3 space-y-3">
        {quote && (
          <blockquote className="flex items-start gap-2 rounded-lg border-l-2 border-accent/50 bg-subtle px-3 py-2 text-sm text-muted">
            <Quote className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            <span className="min-w-0 truncate">{quote}</span>
          </blockquote>
        )}
        <div className="comment-text text-[15px] leading-relaxed" dangerouslySetInnerHTML={{ __html: comment.text }} />
        {comment.attachmentType && <Attachment comment={comment} />}
      </div>
    </article>
  );
}
