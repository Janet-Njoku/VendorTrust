"use client";

import Link from "next/link";
import { useId, useMemo, useState } from "react";

import { VendorFormModal } from "@/components/vendor-form";
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
import { type Bucket, latestScored } from "@/lib/dashboard";
import { usePortfolio } from "@/lib/data";
import { BUCKET_LABEL, formatDate } from "@/lib/format";

type StatusFilter = "all" | "active" | "archived";

export default function VendorsPage() {
  const portfolio = usePortfolio();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [risk, setRisk] = useState<Bucket | "all">("all");
  const [adding, setAdding] = useState(false);
  const searchId = useId();
  const statusId = useId();
  const riskId = useId();

  const latest = useMemo(
    () => latestScored(portfolio.data?.assessments ?? []),
    [portfolio.data],
  );

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (portfolio.data?.vendors ?? []).filter((v) => {
      if (term && !v.name.toLowerCase().includes(term)) return false;
      if (status !== "all" && v.status !== status) return false;
      if (risk !== "all" && (latest.get(v.id)?.risk_level ?? "unassessed") !== risk) return false;
      return true;
    });
  }, [portfolio.data, search, status, risk, latest]);

  const total = portfolio.data?.vendors.length ?? 0;

  return (
    <>
      <PageHeader
        title="Vendors"
        subtitle="Your vendor register, with each vendor's current risk from its latest scored assessment."
        actions={<Button onClick={() => setAdding(true)}>Add vendor</Button>}
      />

      {portfolio.error ? (
        <ErrorState error={portfolio.error} onRetry={portfolio.reload} />
      ) : !portfolio.data ? (
        <div role="status" aria-label="Loading vendors" className="space-y-3">
          <Skeleton className="h-12" />
          <Skeleton className="h-64" />
        </div>
      ) : total === 0 ? (
        <EmptyState title="No vendors yet" action={<Button onClick={() => setAdding(true)}>Add vendor</Button>}>
          Add the third parties you rely on. You can then assess them against a questionnaire.
        </EmptyState>
      ) : (
        <Card className="overflow-hidden" >
          <div className="-m-5">
            <div className="grid gap-3 border-b border-slate-100 p-4 sm:grid-cols-3">
              <div>
                <label htmlFor={searchId} className="sr-only">
                  Search vendors
                </label>
                <input
                  id={searchId}
                  type="search"
                  className={INPUT}
                  placeholder="Search by name…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <div>
                <label htmlFor={statusId} className="sr-only">
                  Filter by status
                </label>
                <select id={statusId} className={INPUT} value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)}>
                  <option value="all">All statuses</option>
                  <option value="active">Active</option>
                  <option value="archived">Archived</option>
                </select>
              </div>
              <div>
                <label htmlFor={riskId} className="sr-only">
                  Filter by risk
                </label>
                <select id={riskId} className={INPUT} value={risk} onChange={(e) => setRisk(e.target.value as Bucket | "all")}>
                  <option value="all">All risk levels</option>
                  {(["critical", "high", "medium", "low", "unassessed"] as Bucket[]).map((b) => (
                    <option key={b} value={b}>
                      {BUCKET_LABEL[b]}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {rows.length === 0 ? (
              <p className="px-5 py-12 text-center text-sm text-slate-600">
                No vendors match these filters.{" "}
                <button
                  type="button"
                  className="font-medium text-cyan-700 hover:underline"
                  onClick={() => {
                    setSearch("");
                    setStatus("all");
                    setRisk("all");
                  }}
                >
                  Clear filters
                </button>
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-100 text-sm">
                  <caption className="sr-only">Vendors</caption>
                  <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th scope="col" className="px-5 py-3 font-medium">Vendor</th>
                      <th scope="col" className="px-3 py-3 font-medium">Status</th>
                      <th scope="col" className="px-3 py-3 font-medium">Risk</th>
                      <th scope="col" className="hidden px-3 py-3 font-medium sm:table-cell">Last assessed</th>
                      <th scope="col" className="hidden px-3 py-3 font-medium md:table-cell">Added</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 bg-white">
                    {rows.map((vendor) => {
                      const assessment = latest.get(vendor.id);
                      return (
                        <tr key={vendor.id} className="hover:bg-slate-50">
                          <td className="px-5 py-3">
                            <Link href={`/vendors/${vendor.id}`} className="font-medium text-cyan-700 hover:underline">
                              {vendor.name}
                            </Link>
                          </td>
                          <td className="px-3 py-3"><StatusBadge status={vendor.status} /></td>
                          <td className="px-3 py-3">
                            <RiskBadge level={assessment?.risk_level ?? "unassessed"} score={assessment?.score} />
                          </td>
                          <td className="hidden px-3 py-3 text-slate-600 sm:table-cell">{formatDate(assessment?.scored_at)}</td>
                          <td className="hidden px-3 py-3 text-slate-600 md:table-cell">{formatDate(vendor.created_at)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <p className="border-t border-slate-100 px-5 py-3 text-xs text-slate-500">
              Showing {rows.length} of {total} vendor{total === 1 ? "" : "s"}
            </p>
          </div>
        </Card>
      )}

      {adding && (
        <VendorFormModal
          onClose={() => setAdding(false)}
          onSaved={() => {
            setAdding(false);
            portfolio.reload();
          }}
        />
      )}
    </>
  );
}
