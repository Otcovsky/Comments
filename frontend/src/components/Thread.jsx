import { useState } from "react";
import { ChevronDown, CornerDownRight, LoaderCircle, RotateCw, TriangleAlert } from "lucide-react";
import { excerpt } from "../format.js";
import CommentCard from "./CommentCard.jsx";
import CommentForm from "./CommentForm.jsx";
import { Button, cx } from "./ui.jsx";

// Deeper levels stop indenting so long threads stay readable.
const MAX_INDENT_DEPTH = 6;

function Replies({ parent, level, thread, replyTo, setReplyTo }) {
  const comments = thread.replies.get(parent.id);

  if (thread.failed.has(parent.id)) {
    return (
      <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-danger" role="status">
        <TriangleAlert className="size-4" aria-hidden="true" />
        Не удалось загрузить ответы.
        <Button size="sm" onClick={() => thread.load(parent.id)}>
          <RotateCw className="size-3.5" aria-hidden="true" />
          Повторить
        </Button>
      </div>
    );
  }
  if (!comments) {
    return (
      <div className="mt-3 flex items-center gap-2 text-sm text-muted">
        <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
        Загрузка ответов…
      </div>
    );
  }
  if (comments.length === 0) {
    return level === 0 && replyTo === null ? (
      <p className="mt-3 pl-1 text-sm text-muted">Ответов пока нет — станьте первым.</p>
    ) : null;
  }
  return (
    <div
      className={
        level < MAX_INDENT_DEPTH
          ? "mt-3 ml-3 space-y-3 border-l-2 border-line pl-3 sm:ml-5 sm:pl-5"
          : "mt-3 space-y-3"
      }
    >
      {comments.map((comment) => (
        <CommentNode
          key={comment.id}
          node={comment}
          parent={parent}
          level={level + 1}
          thread={thread}
          replyTo={replyTo}
          setReplyTo={setReplyTo}
        />
      ))}
    </div>
  );
}

function CommentNode({ node, parent, level, thread, replyTo, setReplyTo }) {
  const replying = replyTo === node.id;
  const open = thread.isOpen(node.id);

  const onReplied = () => {
    setReplyTo(null);
    thread.showReplies(node.id, level);
  };

  return (
    <div className="animate-fade-in">
      <CommentCard
        comment={node}
        quote={parent ? excerpt(parent.text) : null}
        replying={replying}
        onReply={() => setReplyTo(replying ? null : node.id)}
      />
      {replying && (
        <div className="mt-3 animate-slide-down rounded-xl border border-accent/30 bg-surface p-4 shadow-sm ring-4 ring-accent-soft">
          <p className="mb-4 flex items-center gap-1.5 text-sm text-muted">
            <CornerDownRight className="size-4" aria-hidden="true" />
            Ответ для <span className="font-medium text-fg">{node.username}</span>
          </p>
          <CommentForm parentId={node.id} onSuccess={onReplied} onCancel={() => setReplyTo(null)} />
        </div>
      )}
      {/* The top-level comment is open for as long as its thread is. */}
      {level > 0 && (open || node.replyCount > 0) && (
        <Button
          variant="ghost"
          size="sm"
          className="mt-2"
          aria-expanded={open}
          onClick={() => thread.toggleBranch(node.id, level)}
        >
          <ChevronDown className={cx("size-4 transition-transform", open && "rotate-180")} aria-hidden="true" />
          {open ? "Скрыть ответы" : `Показать ответы (${node.replyCount})`}
        </Button>
      )}
      {open && <Replies parent={node} level={level} thread={thread} replyTo={replyTo} setReplyTo={setReplyTo} />}
    </div>
  );
}

export default function Thread({ root, thread }) {
  const [replyTo, setReplyTo] = useState(null);

  return (
    <div className="animate-fade-in">
      <CommentNode node={root} parent={null} level={0} thread={thread} replyTo={replyTo} setReplyTo={setReplyTo} />
    </div>
  );
}
