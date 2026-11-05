import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";

const NO_THREAD = { rootId: null, branches: [] };

// A comment and every comment below it, as far as replies are loaded.
function withDescendants(replies, id) {
  const ids = new Set([id]);
  const walk = (parentId) =>
    (replies.get(parentId) || []).forEach((comment) => {
      ids.add(comment.id);
      walk(comment.id);
    });
  walk(id);
  return ids;
}

// The open thread. A branch is a comment whose replies are unfolded; its level is the
// comment's depth, 0 for the top-level comment. The branches go to the server as part
// of the viewport.
export function useThread() {
  const [view, setView] = useState(NO_THREAD);
  // Parent id -> its direct replies.
  const [replies, setReplies] = useState(() => new Map());
  const [failed, setFailed] = useState(() => new Set());
  const lastRequest = useRef(0);
  // Parent id -> number of its latest request: only that one may update the replies.
  const requests = useRef(new Map());

  const load = useCallback((parentId) => {
    const current = ++lastRequest.current;
    requests.current.set(parentId, current);
    const setFailure = (isFailed) =>
      setFailed((previous) => {
        const next = new Set(previous);
        if (isFailed) next.add(parentId);
        else next.delete(parentId);
        return next;
      });
    api
      .getReplies(parentId)
      .then((comments) => {
        if (requests.current.get(parentId) !== current) return;
        setReplies((previous) => new Map(previous).set(parentId, comments));
        setFailure(false);
      })
      .catch(() => requests.current.get(parentId) === current && setFailure(true));
  }, []);

  useEffect(() => {
    for (const { id } of view.branches) {
      if (!requests.current.has(id)) load(id);
    }
  }, [view.branches, load]);

  const reset = (next) => {
    requests.current.clear();
    setReplies(new Map());
    setFailed(new Set());
    setView(next);
  };

  const isOpen = (id) => view.branches.some((branch) => branch.id === id);

  const openBranch = (id, level) =>
    setView((current) =>
      current.branches.some((branch) => branch.id === id)
        ? current
        : { ...current, branches: [...current.branches, { id, level }] },
    );

  const closeBranch = (id) => {
    const closed = withDescendants(replies, id);
    closed.forEach((closedId) => requests.current.delete(closedId));
    setReplies((previous) => new Map([...previous].filter(([parentId]) => !closed.has(parentId))));
    setView((current) => ({ ...current, branches: current.branches.filter((branch) => !closed.has(branch.id)) }));
  };

  return {
    rootId: view.rootId,
    branches: view.branches,
    replies,
    failed,
    isOpen,
    load,
    toggleBranch: (id, level) => (isOpen(id) ? closeBranch(id) : openBranch(id, level)),
    toggleRoot: (id) => reset(view.rootId === id ? NO_THREAD : { rootId: id, branches: [{ id, level: 0 }] }),
    close: () => reset(NO_THREAD),
    // After the author's own reply: the branch must show it, even if it was folded.
    showReplies: (id, level) => {
      if (requests.current.has(id)) load(id);
      else openBranch(id, level);
    },
    reloadBranch: (id) => {
      if (isOpen(id)) load(id);
    },
    reloadAll: () => view.branches.forEach(({ id }) => load(id)),
    setReplyCount: (id, replyCount) =>
      setReplies(
        (previous) =>
          new Map(
            [...previous].map(([parentId, comments]) => [
              parentId,
              comments.map((comment) => (comment.id === id ? { ...comment, replyCount } : comment)),
            ]),
          ),
      ),
  };
}
