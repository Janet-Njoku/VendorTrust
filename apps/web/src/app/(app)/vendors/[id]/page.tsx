"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { useApi } from "@/components/providers";
import { StartAssessmentModal } from "@/components/start-assessment";
import { VendorFormModal } from "@/components/vendor-form";
import {
  ActionError,
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  RiskBadge,
  StatusBadge,
} from "@/components/ui";
import { fetchAll } from "@/lib/api/client";
import { latestScored } from "@/lib/dashboard";
import { formatDate } from "@/lib/format";
import { useAction, useQuery } from "@/lib/hooks";

export default function VendorDetailPage() {
  const { id } = useParams<{ id: string }>();
  const api = useApi();
  const [editing, setEditing] = useState(false);
  const [assessing, setAssessing] = useState(false);

  const vendor = useQuery(() => api.getVendor(id), `vendor-${id}`);
  const assessments = useQuery(
    () => fetchAll((p) => api.listAssessments({ ...p, vendorId: id })),
    `vendor-assessments-${id}`,
  );
  const toggle = useAction(async () => {
    if (!vendor.data) return;
    await api.updateVendor(id, { status: vendor.data.status === "active" ? "archived" : "active" });
    vendor.reload();
  });

  if (vendor.error) return <ErrorState error={vendor.error} onRetry={vendor.reload} />;
  if (!vendor.data) return <LoadingState label="Loading vendor…" />;
  const v = vendor.data;
  const current = latestScored(assessments.data ?? []).get(v.id);

  return (
    <>
      <p className="mb-3 text-sm">
        <Link href="/vendors" className="text-cyan-700 hover:underline">
          ← All vendors
        </Link>
      </p>
      <PageHeader
        title={v.name}
        subtitle={
          <span className="inline-flex items-center gap-2">
            <StatusBadge status={v.status} /> Added {formatDate(v.created_at)}
          </span>
        }
        actions={
          <>
            <Button variant="secondary" onClick={() => setEditing(true)}>
              Edit
            </Button>
            <Button variant="secondary" loading={toggle.pending} onClick={() => void toggle.run()}>
              {v.status === "active" ? "Archive" : "Restore"}
            </Button>
            <Button disabled={v.status !== "active"} onClick={() => setAssessing(true)}>
              Start assessment
            </Button>
          </>
        }
      />
      <div className="mb-4">
        <ActionError error={toggle.error} />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Current risk">
          {assessments.data === undefined ? (
            <p className="text-sm text-slate-500">Loading…</p>
          ) : current ? (
            <div className="space-y-2">
              <RiskBadge level={current.risk_level ?? "unassessed"} score={current.score} />
              <p className="text-sm text-slate-600">Scored {formatDate(current.scored_at)}</p>
              <Link href={`/assessments/${current.id}`} className="text-sm font-medium text-cyan-700 hover:underline">
                View the result
              </Link>
            </div>
          ) : (
            <p className="text-sm text-slate-600">Not assessed yet.</p>
          )}
        </Card>

        <div className="lg:col-span-2">
          <Card title="Assessments">
            {assessments.error ? (
              <ErrorState error={assessments.error} onRetry={assessments.reload} />
            ) : assessments.data === undefined ? (
              <p className="text-sm text-slate-500">Loading…</p>
            ) : assessments.data.length === 0 ? (
              <EmptyState title="No assessments for this vendor">
                Start one to score this vendor against a questionnaire.
              </EmptyState>
            ) : (
              <ul className="divide-y divide-slate-100">
                {assessments.data.map((a) => (
                  <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-sm">
                    <Link href={`/assessments/${a.id}`} className="font-medium text-cyan-700 hover:underline">
                      Started {formatDate(a.created_at)}
                    </Link>
                    <span className="flex items-center gap-2">
                      <StatusBadge status={a.status} />
                      {a.status === "scored" && <RiskBadge level={a.risk_level ?? "unassessed"} score={a.score} />}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>

      {editing && (
        <VendorFormModal
          vendor={v}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            vendor.reload();
          }}
        />
      )}
      {assessing && <StartAssessmentModal vendor={v} onClose={() => setAssessing(false)} />}
    </>
  );
}
