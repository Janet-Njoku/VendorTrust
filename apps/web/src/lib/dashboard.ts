/**
 * Dashboard figures, computed only from what the API returns for vendors and assessments.
 * There is no dashboard endpoint and no audit-log endpoint, so "recent activity" is derived from
 * record timestamps and is labelled as such in the UI.
 */

import type { Assessment, RiskLevel, Vendor } from "./api/types";

export type Bucket = RiskLevel | "unassessed";
export const BUCKETS: Bucket[] = ["critical", "high", "medium", "low", "unassessed"];

/** The most recently scored assessment of each vendor: its current risk position. */
export function latestScored(assessments: Assessment[]): Map<string, Assessment> {
  const latest = new Map<string, Assessment>();
  for (const a of assessments) {
    if (a.status !== "scored" || !a.scored_at || a.score === null || a.risk_level === null) continue;
    const current = latest.get(a.vendor_id);
    if (!current || (current.scored_at ?? "") < a.scored_at) latest.set(a.vendor_id, a);
  }
  return latest;
}

export function riskDistribution(
  vendors: Vendor[],
  latest: Map<string, Assessment>,
): Record<Bucket, number> {
  const counts: Record<Bucket, number> = {
    critical: 0,
    high: 0,
    medium: 0,
    low: 0,
    unassessed: 0,
  };
  for (const vendor of vendors) {
    if (vendor.status !== "active") continue;
    const level = latest.get(vendor.id)?.risk_level;
    counts[level ?? "unassessed"] += 1;
  }
  return counts;
}

export interface RiskyVendor {
  vendor: Vendor;
  assessment: Assessment;
}

/** Active vendors whose latest scored assessment is high or critical, worst first. */
export function highRiskVendors(vendors: Vendor[], latest: Map<string, Assessment>): RiskyVendor[] {
  const out: RiskyVendor[] = [];
  for (const vendor of vendors) {
    const assessment = latest.get(vendor.id);
    if (
      vendor.status === "active" &&
      assessment &&
      (assessment.risk_level === "high" || assessment.risk_level === "critical")
    ) {
      out.push({ vendor, assessment });
    }
  }
  return out.sort(
    (a, b) =>
      (b.assessment.score ?? 0) - (a.assessment.score ?? 0) ||
      a.vendor.name.localeCompare(b.vendor.name),
  );
}

export function assessmentProgress(assessments: Assessment[]) {
  const scored = assessments.filter((a) => a.status === "scored").length;
  return { inProgress: assessments.length - scored, scored, total: assessments.length };
}

export interface ActivityEvent {
  id: string;
  at: string;
  kind: "vendor_added" | "vendor_updated" | "assessment_started" | "assessment_scored";
  label: string;
  href: string;
}

export function recentActivity(
  vendors: Vendor[],
  assessments: Assessment[],
  limit = 8,
): ActivityEvent[] {
  const names = new Map(vendors.map((v) => [v.id, v.name]));
  const events: ActivityEvent[] = [];
  for (const v of vendors) {
    events.push({
      id: `vendor-created-${v.id}`,
      at: v.created_at,
      kind: "vendor_added",
      label: `Vendor added: ${v.name}`,
      href: `/vendors/${v.id}`,
    });
    if (v.updated_at > v.created_at) {
      events.push({
        id: `vendor-updated-${v.id}`,
        at: v.updated_at,
        kind: "vendor_updated",
        label: `Vendor updated: ${v.name}`,
        href: `/vendors/${v.id}`,
      });
    }
  }
  for (const a of assessments) {
    const vendor = names.get(a.vendor_id) ?? "a vendor";
    events.push({
      id: `assessment-started-${a.id}`,
      at: a.created_at,
      kind: "assessment_started",
      label: `Assessment started for ${vendor}`,
      href: `/assessments/${a.id}`,
    });
    if (a.status === "scored" && a.scored_at) {
      events.push({
        id: `assessment-scored-${a.id}`,
        at: a.scored_at,
        kind: "assessment_scored",
        label: `Assessment scored for ${vendor}: ${a.score?.toFixed(1)} (${a.risk_level})`,
        href: `/assessments/${a.id}`,
      });
    }
  }
  return events.sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : a.id.localeCompare(b.id))).slice(0, limit);
}
