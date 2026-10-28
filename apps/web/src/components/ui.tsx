"use client";

import { useEffect, useId, useRef, useState } from "react";

import { type ApiError, describeKind } from "@/lib/api/errors";
import type { Bucket } from "@/lib/dashboard";
import { BUCKET_LABEL, BUCKET_STYLE } from "@/lib/format";
import { isValidOrgId } from "@/lib/auth/storage";

import { MfaGate } from "./access";
import { useAuth } from "./providers";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

// --- buttons ---

type Variant = "primary" | "secondary" | "danger" | "ghost";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-cyan-600 text-white hover:bg-cyan-700 focus-visible:outline-cyan-600 disabled:bg-cyan-300",
  secondary:
    "bg-white text-slate-800 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 disabled:text-slate-400",
  danger: "bg-red-600 text-white hover:bg-red-700 focus-visible:outline-red-600 disabled:bg-red-300",
  ghost: "text-slate-700 hover:bg-slate-100 disabled:text-slate-400",
};

export function Button({
  variant = "primary",
  loading = false,
  className,
  children,
  disabled,
  type = "button",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; loading?: boolean }) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-semibold shadow-sm transition focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed",
        VARIANTS[variant],
        className,
      )}
      {...props}
    >
      {loading && <Spinner className="h-4 w-4" />}
      {children}
    </button>
  );
}

export function Spinner({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={cx("animate-spin", className)} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" className="opacity-25" />
      <path d="M4 12a8 8 0 018-8" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
    </svg>
  );
}

// --- layout ---

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="truncate text-2xl font-semibold tracking-tight text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-600">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({
  title,
  action,
  children,
  className,
}: {
  title?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("rounded-xl border border-slate-200 bg-white shadow-sm", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-2 border-b border-slate-100 px-5 py-3.5">
          {title && <h2 className="text-sm font-semibold text-slate-900">{title}</h2>}
          {action}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

// --- badges ---

export function RiskBadge({ level, score }: { level: Bucket; score?: number | null }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
        BUCKET_STYLE[level].badge,
      )}
    >
      {BUCKET_LABEL[level]}
      {score !== undefined && score !== null && <span className="font-mono">{score.toFixed(1)}</span>}
    </span>
  );
}

const STATUS_STYLE: Record<string, string> = {
  active: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  published: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  scored: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  draft: "bg-sky-50 text-sky-700 ring-sky-600/20",
  in_progress: "bg-sky-50 text-sky-700 ring-sky-600/20",
  archived: "bg-slate-100 text-slate-600 ring-slate-500/20",
  retired: "bg-slate-100 text-slate-600 ring-slate-500/20",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={cx(
        "inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        STATUS_STYLE[status] ?? STATUS_STYLE.archived,
      )}
    >
      {status.replace("_", " ")}
    </span>
  );
}

// --- states ---

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-3 py-16 text-sm text-slate-500">
      <Spinner />
      <span>{label}</span>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cx("animate-pulse rounded-md bg-slate-200", className)} />;
}

export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-slate-300 bg-white px-6 py-14 text-center">
      <h3 className="text-base font-semibold text-slate-900">{title}</h3>
      {children && <p className="mt-1 max-w-md text-sm text-slate-600">{children}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function Notice({
  tone = "info",
  children,
}: {
  tone?: "info" | "error" | "success" | "warning";
  children: React.ReactNode;
}) {
  const style = {
    info: "border-sky-200 bg-sky-50 text-sky-900",
    error: "border-red-200 bg-red-50 text-red-900",
    success: "border-emerald-200 bg-emerald-50 text-emerald-900",
    warning: "border-amber-200 bg-amber-50 text-amber-900",
  }[tone];
  return (
    <div role={tone === "error" ? "alert" : "status"} className={cx("rounded-lg border px-4 py-3 text-sm", style)}>
      {children}
    </div>
  );
}

/** An inline error for a failed action. */
export function ActionError({ error }: { error: ApiError | undefined }) {
  if (!error) return null;
  return <Notice tone="error">{describeKind(error)}</Notice>;
}

/**
 * A full error for a screen that could not load. Two kinds need the user's help rather than a retry:
 * several organizations (choose one) and no membership yet (provisioning is an admin task).
 */
export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const auth = useAuth();
  if (error.kind === "no_membership") {
    return (
      <EmptyState title="You do not have access to an organization yet">
        You are signed in, but no organization has granted your account a membership. An organization
        administrator must add you before data appears here. First-organization setup and invitations are
        not available in the app yet.
      </EmptyState>
    );
  }
  if (error.kind === "org_required") {
    return <OrgPrompt onSaved={onRetry} />;
  }
  if (error.kind === "mfa_required") {
    return <MfaGate onDone={() => onRetry?.()} />;
  }
  return (
    <div role="alert" className="rounded-xl border border-red-200 bg-red-50 px-6 py-8 text-center">
      <h3 className="text-base font-semibold text-red-900">
        {error.kind === "not_found" ? "Not found" : "Something went wrong"}
      </h3>
      <p className="mt-1 text-sm text-red-800">{describeKind(error)}</p>
      <div className="mt-4 flex justify-center gap-2">
        {onRetry && error.kind !== "not_found" && (
          <Button variant="secondary" onClick={onRetry}>
            Try again
          </Button>
        )}
        {error.kind === "unauthenticated" && <Button onClick={() => void auth.signOut()}>Sign in</Button>}
      </div>
    </div>
  );
}

export function OrgPrompt({ onSaved }: { onSaved?: () => void }) {
  const auth = useAuth();
  const [value, setValue] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const id = useId();
  return (
    <form
      className="mx-auto max-w-md rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
      onSubmit={(event) => {
        event.preventDefault();
        if (!isValidOrgId(value)) {
          setProblem("Enter the organization ID as a UUID.");
          return;
        }
        auth.setOrgId(value);
        setProblem(null);
        onSaved?.();
      }}
    >
      <h3 className="text-base font-semibold text-slate-900">Choose an organization</h3>
      <p className="mt-1 text-sm text-slate-600">
        Your account belongs to more than one organization. Enter the ID of the one to work in. It only
        selects among your own memberships; the server decides what you may see.
      </p>
      <Field label="Organization ID" htmlFor={id} error={problem ?? undefined}>
        <input
          id={id}
          className={INPUT}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="00000000-0000-0000-0000-000000000000"
          spellCheck={false}
          autoComplete="off"
        />
      </Field>
      <Button type="submit" className="mt-4">
        Use this organization
      </Button>
    </form>
  );
}

// --- forms ---

export const INPUT =
  "block w-full rounded-md border-0 px-3 py-2 text-sm text-slate-900 shadow-sm ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:ring-2 focus:ring-inset focus:ring-cyan-600 disabled:bg-slate-50 disabled:text-slate-500";

export function Field({
  label,
  htmlFor,
  error,
  hint,
  children,
}: {
  label: string;
  htmlFor: string;
  error?: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mt-4 first:mt-0">
      <label htmlFor={htmlFor} className="mb-1 block text-sm font-medium text-slate-700">
        {label}
      </label>
      {children}
      {hint && !error && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
      {error && (
        <p role="alert" className="mt-1 text-xs font-medium text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}

export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLElement>("input, select, textarea, button")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/50 p-4 sm:items-center">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl bg-white p-6 shadow-xl"
      >
        <h2 id={titleId} className="text-lg font-semibold text-slate-900">
          {title}
        </h2>
        <div className="mt-4">{children}</div>
      </div>
    </div>
  );
}

export function ProgressBar({ value, max, label }: { value: number; max: number; label: string }) {
  const percent = max === 0 ? 0 : Math.round((value / max) * 100);
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={max}
      className="h-2 w-full overflow-hidden rounded-full bg-slate-200"
    >
      <div className="h-full rounded-full bg-cyan-600 transition-all" style={{ width: `${percent}%` }} />
    </div>
  );
}
