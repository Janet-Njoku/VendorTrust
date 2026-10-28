"use client";

import Link from "next/link";
import { useId, useMemo, useState } from "react";

import { StartAssessmentModal } from "@/components/start-assessment";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  INPUT,
  PageHeader,
  RiskBadge,
  Skeleton,
  StatusBadge,
} from "@/components/ui";
import type { AssessmentStatus } from "@/lib/api/types";
import { usePortfolio } from "@/lib/data";
import { formatDate } from "@/lib/format";

export default function AssessmentsPage() {
  const portfolio = usePortfolio();
  const [status, setStatus] = useState<AssessmentStatus | "all">("all");
  const [vendor, setVendor] = useState("");
  const [starting, setStarting] = useState(false);
  const statusId = useId();
  const vendorId = useId();

  const names = useMemo(
    () => new Map((portfolio.data?.vendors ?? []).map((v) => [v.id, v.name])),
    [portfolio.data],
  );
  const rows = useMemo(
    () =>
      (portfolio.data?.assessments ?? []).filter(
        (a) => (status === "all" || a.status === status) && (!vendor || a.vendor_id === vendor),
      ),
    [portfolio.data, status, vendor],
  );
  const total = portfolio.data?.assessments.length ?? 0;

  return (
    <>
      <PageHeader
        title="Assessments"
        subtitle="Questionnaire-based assessments of your vendors, newest first."
        actions={<Button onClick={() => setStarting(true)}>New assessment</Button>}
      />

      {portfolio.error ? (
        <ErrorState error={portfolio.error} onRetry={portfolio.reload} />
      ) : !portfolio.data ? (
        <div role="status" aria-label="Loading assessments" className="space-y-3">
          <Skeleton className="h-12" />
          <Skeleton className="h-56" />
        </div>
      ) : total === 0 ? (
        <EmptyState title="No assessments yet" action={<Button onClick={() => setStarting(true)}>New assessment</Button>}>
          Pick a vendor and a published questionnaire to begin. Answers are saved as you go.
        </EmptyState>
      ) : (
        <Card>
          <div className="-m-5">
            <div className="grid gap-3 border-b border-slate-100 p-4 sm:grid-cols-2">
              <div>
                <label htmlFor={statusId} className="sr-only">
                  Filter by status
                </label>
                <select id={statusId} className={INPUT} value={status} onChange={(e) => setStatus(e.target.value as AssessmentStatus | "all")}>
                  <option value="all">All statuses</option>
                  <option value="in_progress">In progress</option>
                  <option value="scored">Scored</option>
                </select>
              </div>
              <div>
                <label htmlFor={vendorId} className="sr-only">
                  Filter by vendor
                </label>
                <select id={vendorId} className={INPUT} value={vendor} onChange={(e) => setVendor(e.target.value)}>
                  <option value="">All vendors</option>
                  {portfolio.data.vendors.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {rows.length === 0 ? (
              <p className="px-5 py-12 text-center text-sm text-slate-600">No assessments match these filters.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-100 text-sm">
                  <caption className="sr-only">Assessments</caption>
                  <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th scope="col" className="px-5 py-3 font-medium">Vendor</th>
                      <th scope="col" className="px-3 py-3 font-medium">Status</th>
                      <th scope="col" className="px-3 py-3 font-medium">Result</th>
                      <th scope="col" className="hidden px-3 py-3 font-medium sm:table-cell">Started</th>
                      <th scope="col" className="px-3 py-3">
                        <span className="sr-only">Open</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 bg-white">
                    {rows.map((a) => (
                      <tr key={a.id} className="hover:bg-slate-50">
                        <td className="px-5 py-3 font-medium text-slate-900">{names.get(a.vendor_id) ?? "Unknown vendor"}</td>
                        <td className="px-3 py-3"><StatusBadge status={a.status} /></td>
                        <td className="px-3 py-3">
                          {a.status === "scored" ? <RiskBadge level={a.risk_level ?? "unassessed"} score={a.score} /> : <span className="text-slate-400">—</span>}
                        </td>
                        <td className="hidden px-3 py-3 text-slate-600 sm:table-cell">{formatDate(a.created_at)}</td>
                        <td className="px-3 py-3 text-right">
                          <Link href={`/assessments/${a.id}`} className="font-medium text-cyan-700 hover:underline">
                            {a.status === "scored" ? "View result" : "Continue"}
                            <span className="sr-only"> for {names.get(a.vendor_id) ?? "vendor"}</span>
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </Card>
      )}
      {starting && <StartAssessmentModal vendors={portfolio.data?.vendors ?? []} onClose={() => setStarting(false)} />}
    </>
  );
}
