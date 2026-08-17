import type { ButtonHTMLAttributes, ReactNode } from "react";

/** The small set of primitives every panel is built from. */

type Tone = "positive" | "neutral" | "warning" | "danger" | "info";

const TONE_CLASSES: Record<Tone, string> = {
  positive: "bg-emerald-50 text-emerald-700 border-emerald-200",
  neutral: "bg-ink-100 text-ink-600 border-ink-200",
  warning: "bg-amber-50 text-amber-700 border-amber-200",
  danger: "bg-rose-50 text-rose-700 border-rose-200",
  info: "bg-accent-50 text-accent-700 border-accent-200",
};

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: Tone;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium ${TONE_CLASSES[tone]}`}
    >
      {children}
    </span>
  );
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  children: ReactNode;
}

export function Button({
  variant = "secondary",
  className = "",
  ...props
}: ButtonProps) {
  const base =
    "inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50";

  const variants = {
    primary: "bg-accent-600 text-white hover:bg-accent-700",
    secondary: "border border-ink-200 bg-white text-ink-700 hover:bg-ink-50",
    ghost: "text-ink-600 hover:bg-ink-100",
    danger: "border border-rose-200 bg-white text-rose-700 hover:bg-rose-50",
  } as const;

  return <button className={`${base} ${variants[variant]} ${className}`} {...props} />;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-ink-500">
      <span
        aria-hidden="true"
        className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-ink-300 border-t-accent-600"
      />
      <span>{label}</span>
    </span>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-md px-6 py-12 text-center">
      <h2 className="text-base font-semibold text-ink-800">{title}</h2>
      <p className="mt-1.5 text-sm leading-relaxed text-ink-500">{description}</p>
      {action ? <div className="mt-4 flex justify-center">{action}</div> : null}
    </div>
  );
}

export function ErrorNotice({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2.5"
    >
      <p className="text-sm text-rose-800">{message}</p>
      {onRetry ? (
        <button
          onClick={onRetry}
          className="mt-1.5 text-sm font-medium text-rose-700 underline underline-offset-2 hover:text-rose-900"
        >
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function SectionHeading({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-ink-200 px-5 py-3.5">
      <div className="min-w-0">
        <h1 className="text-sm font-semibold text-ink-900">{title}</h1>
        {description ? (
          <p className="mt-0.5 truncate text-xs text-ink-500">{description}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      ) : null}
    </div>
  );
}
