"use client";

import Link from "next/link";

import { RiskDistribution } from "@/components/charts";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  ProgressBar,
  RiskBadge,
  Skeleton,
} from "@/components/ui";
import {
  assessmentProgress,
  highRiskVendors,
  latestScored,
  recentActivity,
  riskDistribution,
} from "@/lib/dashboard";
import type { Assessment, Vendor } from "@/lib/api/types";
import { usePortfolio } from "@/lib/data";
import { timeAgo } from "@/lib/format";

function Stat({ label, value, hint }: { label: string; value: number | string; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-sm text-slate-500">{label}</p>
      <p className="mt-1 text-3xl font-semibold tabular-nums text-slate-900">{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div aria-busy="true" role="status" aria-label="Loading dashboard" className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-28" />
        ))}
      </div>
      <Skeleton className="h-40" />
      <div className="grid gap-6 lg:grid-cols-2">
        <Skeleton className="h-64" />
        <Skeleton className="h-64" />
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const portfolio = usePortfolio();

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle="Vendor risk across your organization, from the latest scored assessment of each vendor."
        actions={
          <Link href="/assessments">
            <Button>New assessment</Button>
          </Link>
        }
      />
      {portfolio.error ? (
        <ErrorState error={portfolio.error} onRetry={portfolio.reload} />
      ) : !portfolio.data ? (
        <DashboardSkeleton />
      ) : (
        <DashboardBody vendors={portfolio.data.vendors} assessments={portfolio.data.assessments} />
      )}
    </>
  );
}

function DashboardBody({
  vendors,
  assessments,
}: {
  vendors: Vendor[];
  assessments: Assessment[];
}) {
  if (vendors.length === 0) {
    return (
      <EmptyState
        title="No vendors yet"
        action={
          <Link href="/vendors">
            <Button>Add your first vendor</Button>
          </Link>
        }
      >
        Add vendors to the register, then start assessments to see risk figures here.
      </EmptyState>
    );
  }
  const latest = latestScored(assessments);
  const distribution = riskDistribution(vendors, latest);
  const risky = highRiskVendors(vendors, latest);
  const progress = assessmentProgress(assessments);
  const activity = recentActivity(vendors, assessments);
  const active = vendors.filter((v) => v.status === "active").length;

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Vendors" value={vendors.length} hint={`${active} active, ${vendors.length - active} archived`} />
        <Stat label="High or critical risk" value={risky.length} hint="Latest scored assessment" />
        <Stat label="Assessments in progress" value={progress.inProgress} hint={`${progress.total} in total`} />
        <Stat
          label="Not yet assessed"
          value={distribution.unassessed}
          hint="Active vendors with no scored assessment"
        />
      </div>

      <Card title="Risk distribution (active vendors)">
        <RiskDistribution counts={distribution} />
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="High-risk vendors">
          {risky.length === 0 ? (
            <p className="text-sm text-slate-600">No active vendor is currently high or critical risk.</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {risky.map(({ vendor, assessment }) => (
                <li key={vendor.id} className="flex items-center justify-between gap-3 py-2.5">
                  <Link href={`/vendors/${vendor.id}`} className="truncate text-sm font-medium text-cyan-700 hover:underline">
                    {vendor.name}
                  </Link>
                  <RiskBadge level={assessment.risk_level ?? "unassessed"} score={assessment.score} />
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Assessment progress">
          <div className="space-y-4">
            <div>
              <div className="mb-1.5 flex justify-between text-sm">
                <span className="text-slate-600">Scored</span>
                <span className="font-semibold tabular-nums">
                  {progress.scored} of {progress.total}
                </span>
              </div>
              <ProgressBar value={progress.scored} max={progress.total} label="Assessments scored" />
            </div>
            <p className="text-sm text-slate-600">
              {progress.inProgress === 0
                ? "No assessments are waiting to be completed."
                : `${progress.inProgress} assessment${progress.inProgress === 1 ? " is" : "s are"} waiting to be completed.`}
            </p>
            <Link href="/assessments" className="text-sm font-medium text-cyan-700 hover:underline">
              View all assessments
            </Link>
          </div>
        </Card>
      </div>

      <Card title="Recent activity">
        {activity.length === 0 ? (
          <p className="text-sm text-slate-600">Nothing has happened yet.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {activity.map((event) => (
              <li key={event.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                <Link href={event.href} className="truncate text-slate-800 hover:text-cyan-700 hover:underline">
                  {event.label}
                </Link>
                <time dateTime={event.at} className="shrink-0 text-xs text-slate-500">
                  {timeAgo(event.at)}
                </time>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-4 text-xs text-slate-500">
          Derived from vendor and assessment records. The audit log is written by the API but is not
          exposed through an endpoint yet.
        </p>
      </Card>
    </div>
  );
}
