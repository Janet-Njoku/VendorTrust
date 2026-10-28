"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { AnswerForm } from "@/components/answer-form";
import { useApi } from "@/components/providers";
import { Results } from "@/components/results";
import { ErrorState, LoadingState, PageHeader, RiskBadge, StatusBadge } from "@/components/ui";
import { formatDate } from "@/lib/format";
import { useQuery } from "@/lib/hooks";

export default function AssessmentPage() {
  const { id } = useParams<{ id: string }>();
  const api = useApi();
  const detail = useQuery(() => api.getAssessment(id), `assessment-${id}`);
  const scored = detail.data?.status === "scored";
  const vendorId = detail.data?.vendor_id ?? "";
  const vendor = useQuery(
    async () => (vendorId ? api.getVendor(vendorId) : null),
    `assessment-vendor-${vendorId}`,
  );
  const result = useQuery(
    async () => (scored ? api.getResult(id) : null),
    `assessment-result-${id}-${scored}`,
  );

  if (detail.error) return <ErrorState error={detail.error} onRetry={detail.reload} />;
  if (!detail.data) return <LoadingState label="Loading assessment…" />;
  const a = detail.data;

  return (
    <>
      <p className="mb-3 text-sm">
        <Link href="/assessments" className="text-cyan-700 hover:underline">
          ← All assessments
        </Link>
      </p>
      <PageHeader
        title={vendor.data ? `Assessment: ${vendor.data.name}` : "Assessment"}
        subtitle={
          <span className="inline-flex flex-wrap items-center gap-2">
            <StatusBadge status={a.status} />
            Started {formatDate(a.created_at)}
            {a.status === "scored" && <RiskBadge level={a.risk_level ?? "unassessed"} score={a.score} />}
            {vendor.data && (
              <Link href={`/vendors/${vendor.data.id}`} className="text-cyan-700 hover:underline">
                View vendor
              </Link>
            )}
          </span>
        }
      />
      {a.status === "scored" ? (
        result.error ? (
          <ErrorState error={result.error} onRetry={result.reload} />
        ) : !result.data ? (
          <LoadingState label="Loading result…" />
        ) : (
          <Results result={result.data} detail={a} />
        )
      ) : (
        <AnswerForm key={a.id} detail={a} onScored={detail.reload} />
      )}
    </>
  );
}
