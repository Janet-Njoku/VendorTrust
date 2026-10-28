import { BUCKETS, type Bucket } from "@/lib/dashboard";
import type { CategoryScore, RiskLevel, Thresholds } from "@/lib/api/types";
import { BUCKET_LABEL, BUCKET_STYLE, titleCase } from "@/lib/format";

import { cx } from "./ui";

/** Stacked bar plus legend: the risk distribution of vendors. */
export function RiskDistribution({ counts }: { counts: Record<Bucket, number> }) {
  const total = BUCKETS.reduce((sum, b) => sum + counts[b], 0);
  return (
    <div>
      <div
        role="img"
        aria-label={BUCKETS.map((b) => `${BUCKET_LABEL[b]}: ${counts[b]}`).join(", ")}
        className="flex h-4 w-full overflow-hidden rounded-full bg-slate-100"
      >
        {total > 0 &&
          BUCKETS.filter((b) => counts[b] > 0).map((b) => (
            <div
              key={b}
              className={BUCKET_STYLE[b].bar}
              style={{ width: `${(counts[b] / total) * 100}%` }}
              title={`${BUCKET_LABEL[b]}: ${counts[b]}`}
            />
          ))}
      </div>
      <ul className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-5">
        {BUCKETS.map((b) => (
          <li key={b} className="flex items-center gap-2 text-sm">
            <span className={cx("h-2.5 w-2.5 rounded-full", BUCKET_STYLE[b].bar)} aria-hidden="true" />
            <span className="text-slate-600">{BUCKET_LABEL[b]}</span>
            <span className="ml-auto font-semibold tabular-nums text-slate-900">{counts[b]}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const GAUGE = { cx: 120, cy: 120, r: 92, stroke: 18 };

function point(score: number, radius = GAUGE.r) {
  const angle = Math.PI * (1 - score / 100);
  return [GAUGE.cx + radius * Math.cos(angle), GAUGE.cy - radius * Math.sin(angle)] as const;
}

function arc(from: number, to: number) {
  const [x1, y1] = point(from);
  const [x2, y2] = point(to);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${GAUGE.r} ${GAUGE.r} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

const GAUGE_COLOUR: Record<RiskLevel, string> = {
  low: "#10b981",
  medium: "#fbbf24",
  high: "#f97316",
  critical: "#ef4444",
};

/** A 0 to 100 gauge with the version's thresholds as coloured bands. */
export function ScoreGauge({
  score,
  level,
  thresholds,
}: {
  score: number;
  level: RiskLevel;
  thresholds: Thresholds;
}) {
  const bands: [number, number, RiskLevel][] = [
    [0, thresholds.low_max, "low"],
    [thresholds.low_max, thresholds.medium_max, "medium"],
    [thresholds.medium_max, thresholds.high_max, "high"],
    [thresholds.high_max, 100, "critical"],
  ];
  const [nx, ny] = point(score, GAUGE.r - 26);
  return (
    <figure className="flex flex-col items-center">
      <svg
        viewBox="0 0 240 140"
        className="w-full max-w-xs"
        role="img"
        aria-label={`Risk score ${score.toFixed(1)} out of 100, ${BUCKET_LABEL[level]} risk`}
      >
        {bands.map(([from, to, band]) => (
          <path
            key={band}
            d={arc(from, to)}
            fill="none"
            stroke={GAUGE_COLOUR[band]}
            strokeWidth={GAUGE.stroke}
            opacity={band === level ? 1 : 0.35}
          />
        ))}
        <line x1={GAUGE.cx} y1={GAUGE.cy} x2={nx} y2={ny} stroke="#0f172a" strokeWidth="3" strokeLinecap="round" />
        <circle cx={GAUGE.cx} cy={GAUGE.cy} r="6" fill="#0f172a" />
        <text x="120" y="108" textAnchor="middle" className="fill-slate-900 text-3xl font-bold">
          {score.toFixed(1)}
        </text>
        <text x="20" y="136" textAnchor="middle" className="fill-slate-500 text-[10px]">
          0
        </text>
        <text x="220" y="136" textAnchor="middle" className="fill-slate-500 text-[10px]">
          100
        </text>
      </svg>
      <figcaption className="mt-1 text-xs text-slate-500">
        Low below {thresholds.low_max} · Medium below {thresholds.medium_max} · High below{" "}
        {thresholds.high_max} · Critical {thresholds.high_max}+
      </figcaption>
    </figure>
  );
}

function levelFor(score: number, t: Thresholds): RiskLevel {
  if (score < t.low_max) return "low";
  if (score < t.medium_max) return "medium";
  if (score < t.high_max) return "high";
  return "critical";
}

/** One horizontal bar per category, coloured by the level its score falls in. */
export function CategoryBars({
  categories,
  thresholds,
}: {
  categories: Record<string, CategoryScore>;
  thresholds: Thresholds;
}) {
  return (
    <ul className="space-y-3">
      {Object.entries(categories).map(([name, c]) => (
        <li key={name}>
          <div className="mb-1 flex items-baseline justify-between text-sm">
            <span className="font-medium text-slate-800">{titleCase(name)}</span>
            <span className="text-slate-500">
              {c.score === null ? (
                "Not applicable"
              ) : (
                <>
                  <span className="font-semibold tabular-nums text-slate-900">{c.score.toFixed(1)}</span>
                  <span className="ml-2 text-xs">weight {c.weight}</span>
                </>
              )}
            </span>
          </div>
          <div className="h-2.5 overflow-hidden rounded-full bg-slate-100">
            {c.score !== null && (
              <div
                className={BUCKET_STYLE[levelFor(c.score, thresholds)].bar}
                style={{ width: `${Math.max(2, c.score)}%`, height: "100%" }}
              />
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
