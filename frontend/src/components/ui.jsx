// Shared UI primitives.

export const cx = (...classes) => classes.filter(Boolean).join(" ");

export const focusRing =
  "outline-none focus-visible:ring-4 focus-visible:ring-accent/25 focus-visible:ring-offset-0";

const BUTTON_VARIANTS = {
  primary: "bg-accent text-white shadow-sm shadow-accent/25 hover:bg-accent-hover",
  secondary: "border border-line bg-surface text-fg shadow-xs hover:bg-subtle",
  ghost: "text-muted hover:bg-subtle hover:text-fg",
};

const BUTTON_SIZES = {
  sm: "h-8 gap-1.5 px-2.5 text-sm",
  md: "h-10 gap-2 px-4 text-sm",
  icon: "size-10",
};

export function Button({ variant = "secondary", size = "md", className, ...props }) {
  return (
    <button
      type="button"
      className={cx(
        "inline-flex shrink-0 cursor-pointer items-center justify-center rounded-lg font-medium transition active:translate-y-px",
        "disabled:pointer-events-none disabled:opacity-60",
        focusRing,
        BUTTON_VARIANTS[variant],
        BUTTON_SIZES[size],
        className,
      )}
      {...props}
    />
  );
}

export function EmptyState({ icon: Icon, tone = "muted", title, text, action }) {
  return (
    <div className="flex flex-col items-center px-6 py-16 text-center animate-fade-in">
      <span
        className={cx(
          "mb-4 grid size-12 place-items-center rounded-2xl",
          tone === "danger" ? "bg-danger-soft text-danger" : "bg-accent-soft text-accent-ink",
        )}
      >
        <Icon className="size-6" aria-hidden="true" />
      </span>
      <p className="font-semibold">{title}</p>
      {text && <p className="mt-1 max-w-sm text-sm text-muted">{text}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
