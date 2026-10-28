import type { Assessment, Vendor } from "./api/types";
import {
  assessmentProgress,
  highRiskVendors,
  latestScored,
  recentActivity,
  riskDistribution,
} from "./dashboard";

function vendor(id: string, overrides: Partial<Vendor> = {}): Vendor {
  return {
    id,
    org_id: "org",
    name: `Vendor ${id}`,
    status: "active",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function assessment(id: string, vendorId: string, overrides: Partial<Assessment> = {}): Assessment {
  return {
    id,
    org_id: "org",
    vendor_id: vendorId,
    version_id: "v1",
    status: "in_progress",
    created_by: "u",
    score: null,
    risk_level: null,
    scored_at: null,
    scored_by: null,
    created_at: "2026-02-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z",
    ...overrides,
  };
}

const scored = (id: string, vendorId: string, score: number, level: Assessment["risk_level"], at: string) =>
  assessment(id, vendorId, { status: "scored", score, risk_level: level, scored_at: at });

describe("latestScored", () => {
  it("uses the most recently scored assessment of each vendor and ignores unscored ones", () => {
    const latest = latestScored([
      scored("a1", "v1", 80, "critical", "2026-03-01T00:00:00Z"),
      scored("a2", "v1", 30, "medium", "2026-04-01T00:00:00Z"),
      assessment("a3", "v1", { created_at: "2026-05-01T00:00:00Z" }),
      scored("a4", "v2", 10, "low", "2026-02-01T00:00:00Z"),
    ]);
    expect(latest.get("v1")?.id).toBe("a2");
    expect(latest.get("v2")?.id).toBe("a4");
    expect(latest.size).toBe(2);
  });
});

describe("riskDistribution", () => {
  it("counts active vendors by current level and the rest as not assessed", () => {
    const vendors = [vendor("v1"), vendor("v2"), vendor("v3"), vendor("v4", { status: "archived" })];
    const latest = latestScored([
      scored("a1", "v1", 90, "critical", "2026-03-01T00:00:00Z"),
      scored("a2", "v2", 20, "low", "2026-03-01T00:00:00Z"),
      scored("a4", "v4", 90, "critical", "2026-03-01T00:00:00Z"),
    ]);
    expect(riskDistribution(vendors, latest)).toEqual({
      critical: 1,
      high: 0,
      medium: 0,
      low: 1,
      unassessed: 1,
    });
  });
});

describe("highRiskVendors", () => {
  it("lists active high and critical vendors, worst first, then by name", () => {
    const vendors = [vendor("v1", { name: "Beta" }), vendor("v2", { name: "Alpha" }), vendor("v3"), vendor("v4"), vendor("v5", { status: "archived" })];
    const latest = latestScored([
      scored("a1", "v1", 60, "high", "2026-03-01T00:00:00Z"),
      scored("a2", "v2", 60, "high", "2026-03-01T00:00:00Z"),
      scored("a3", "v3", 88, "critical", "2026-03-01T00:00:00Z"),
      scored("a4", "v4", 40, "medium", "2026-03-01T00:00:00Z"),
      scored("a5", "v5", 99, "critical", "2026-03-01T00:00:00Z"),
    ]);
    expect(highRiskVendors(vendors, latest).map((r) => r.vendor.id)).toEqual(["v3", "v2", "v1"]);
  });
});

describe("assessmentProgress", () => {
  it("counts scored and in-progress assessments", () => {
    expect(
      assessmentProgress([
        assessment("a", "v"),
        assessment("b", "v"),
        scored("c", "v", 10, "low", "2026-03-01T00:00:00Z"),
      ]),
    ).toEqual({ inProgress: 2, scored: 1, total: 3 });
    expect(assessmentProgress([])).toEqual({ inProgress: 0, scored: 0, total: 0 });
  });
});

describe("recentActivity", () => {
  it("merges vendor and assessment events, newest first, and respects the limit", () => {
    const vendors = [
      vendor("v1", { name: "Acme", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }),
      vendor("v2", { name: "Globex", created_at: "2026-01-02T00:00:00Z", updated_at: "2026-03-05T00:00:00Z" }),
    ];
    const assessments = [
      assessment("a1", "v1", { created_at: "2026-02-01T00:00:00Z" }),
      scored("a2", "v2", 42.5, "medium", "2026-03-10T00:00:00Z"),
    ];
    const events = recentActivity(vendors, assessments, 5);
    expect(events.map((e) => e.kind)).toEqual([
      "assessment_scored",
      "vendor_updated",
      "assessment_started",
      "assessment_started",
      "vendor_added",
    ]);
    expect(events[0]?.label).toBe("Assessment scored for Globex: 42.5 (medium)");
    expect(events[0]?.href).toBe("/assessments/a2");
    expect(recentActivity(vendors, assessments, 2)).toHaveLength(2);
  });

  it("is empty when nothing exists", () => {
    expect(recentActivity([], [])).toEqual([]);
  });
});
