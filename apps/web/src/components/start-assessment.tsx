"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";

import type { Vendor } from "@/lib/api/types";
import { useAction, useQuery } from "@/lib/hooks";

import { useApi } from "./providers";
import { ActionError, Button, ErrorState, Field, INPUT, LoadingState, Modal, Notice } from "./ui";

/**
 * Start an assessment: pick a vendor (unless one is given) and a questionnaire. The API chooses the
 * questionnaire's latest published version and refuses with a clear message if there is none.
 */
export function StartAssessmentModal({
  vendor,
  vendors,
  onClose,
}: {
  vendor?: Vendor;
  vendors?: Vendor[];
  onClose: () => void;
}) {
  const api = useApi();
  const router = useRouter();
  const vendorId = useId();
  const templateId = useId();
  const templates = useQuery(() => api.listTemplates({ limit: 100 }), "templates-active");
  const [chosenVendor, setChosenVendor] = useState(vendor?.id ?? "");
  const [chosenTemplate, setChosenTemplate] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const start = useAction(async () =>
    api.createAssessment(chosenVendor, { questionnaire_id: chosenTemplate }),
  );

  const activeVendors = (vendors ?? []).filter((v) => v.status === "active");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!chosenVendor) return setProblem("Choose a vendor.");
    if (!chosenTemplate) return setProblem("Choose a questionnaire.");
    setProblem(null);
    const created = await start.run();
    if (created) router.push(`/assessments/${created.id}`);
  }

  return (
    <Modal title={vendor ? `Assess ${vendor.name}` : "New assessment"} onClose={onClose}>
      {templates.error ? (
        <ErrorState error={templates.error} onRetry={templates.reload} />
      ) : !templates.data ? (
        <LoadingState label="Loading questionnaires…" />
      ) : templates.data.length === 0 ? (
        <Notice tone="warning">
          There are no questionnaires yet. Create one from the starter on the Questionnaires page, publish it,
          then come back.
        </Notice>
      ) : (
        <form onSubmit={submit} noValidate>
          {!vendor && (
            <Field label="Vendor" htmlFor={vendorId}>
              <select id={vendorId} className={INPUT} value={chosenVendor} onChange={(e) => setChosenVendor(e.target.value)}>
                <option value="">Select a vendor…</option>
                {activeVendors.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <Field
            label="Questionnaire"
            htmlFor={templateId}
            hint="The latest published version is used and then frozen for this assessment."
          >
            <select id={templateId} className={INPUT} value={chosenTemplate} onChange={(e) => setChosenTemplate(e.target.value)}>
              <option value="">Select a questionnaire…</option>
              {templates.data.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </Field>
          {problem && (
            <p role="alert" className="mt-3 text-sm font-medium text-red-700">
              {problem}
            </p>
          )}
          <div className="mt-4">
            <ActionError error={start.error} />
          </div>
          <div className="mt-6 flex justify-end gap-2">
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={start.pending}>
              Start assessment
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}
