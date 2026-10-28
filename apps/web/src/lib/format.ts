import type { Bucket } from "./dashboard";

const DATE = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const DATE_TIME = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : DATE.format(date);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : DATE_TIME.format(date);
}

export function timeAgo(iso: string, now = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days < 30 ? `${days} d ago` : formatDate(iso);
}

export function titleCase(slug: string): string {
  return slug
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export const BUCKET_LABEL: Record<Bucket, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
  unassessed: "Not assessed",
};

/** Control-validation colours: one hue per level, readable on light and dark surfaces. */
export const BUCKET_STYLE: Record<Bucket, { badge: string; bar: string; text: string }> = {
  critical: {
    badge: "bg-red-100 text-red-800 ring-red-600/20",
    bar: "bg-red-500",
    text: "text-red-700",
  },
  high: {
    badge: "bg-orange-100 text-orange-800 ring-orange-600/20",
    bar: "bg-orange-500",
    text: "text-orange-700",
  },
  medium: {
    badge: "bg-amber-100 text-amber-800 ring-amber-600/20",
    bar: "bg-amber-400",
    text: "text-amber-700",
  },
  low: {
    badge: "bg-emerald-100 text-emerald-800 ring-emerald-600/20",
    bar: "bg-emerald-500",
    text: "text-emerald-700",
  },
  unassessed: {
    badge: "bg-slate-100 text-slate-700 ring-slate-500/20",
    bar: "bg-slate-300",
    text: "text-slate-600",
  },
};

/** Weighted contribution of one question to the overall score, in score points. */
export function contribution(weight: number, riskPoints: number | null, applicableWeight: number) {
  if (riskPoints === null || applicableWeight === 0) return 0;
  return (weight * riskPoints) / applicableWeight;
}
