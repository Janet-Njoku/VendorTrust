"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";

import { useApi } from "@/components/providers";
import {
  ActionError,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  INPUT,
  LoadingState,
  Modal,
  PageHeader,
  StatusBadge,
} from "@/components/ui";
import { formatDate } from "@/lib/format";
import { useAction, useQuery } from "@/lib/hooks";

function CreateTemplateModal({ onClose }: { onClose: () => void }) {
  const api = useApi();
  const router = useRouter();
  const nameId = useId();
  const descriptionId = useId();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const create = useAction(() =>
    api.createTemplate({ name: name.trim(), description: description.trim() || null }),
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (name.trim().length === 0) return setProblem("Give the questionnaire a name.");
    if (name.trim().length > 200) return setProblem("The name must be 200 characters or fewer.");
    setProblem(null);
    const created = await create.run();
    if (created) router.push(`/questionnaires/${created.id}`);
  }

  return (
    <Modal title="New questionnaire" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <Field label="Name" htmlFor={nameId} error={problem ?? create.error?.fieldErrors.name}>
          <input id={nameId} className={INPUT} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Description (optional)" htmlFor={descriptionId}>
          <textarea
            id={descriptionId}
            className={INPUT}
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
        <div className="mt-4">
          <ActionError error={create.error && !create.error.fieldErrors.name ? create.error : undefined} />
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={create.pending}>
            Create questionnaire
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export default function QuestionnairesPage() {
  const api = useApi();
  const router = useRouter();
  const [showArchived, setShowArchived] = useState(false);
  const [creating, setCreating] = useState(false);
  const archivedId = useId();
  const templates = useQuery(
    () => api.listTemplates({ limit: 100, includeArchived: showArchived }),
    `templates-${showArchived}`,
  );
  const starter = useAction(async () => {
    const created = await api.createFromStarter();
    router.push(`/questionnaires/${created.id}`);
  });

  return (
    <>
      <PageHeader
        title="Questionnaires"
        subtitle="Reusable security questionnaires. Versions are frozen once published, so past assessments never change."
        actions={
          <>
            <Button variant="secondary" loading={starter.pending} onClick={() => void starter.run()}>
              Create from starter
            </Button>
            <Button onClick={() => setCreating(true)}>New questionnaire</Button>
          </>
        }
      />
      <div className="mb-4">
        <ActionError error={starter.error} />
      </div>

      {templates.error ? (
        <ErrorState error={templates.error} onRetry={templates.reload} />
      ) : !templates.data ? (
        <LoadingState label="Loading questionnaires…" />
      ) : templates.data.length === 0 ? (
        <EmptyState
          title="No questionnaires yet"
          action={
            <Button loading={starter.pending} onClick={() => void starter.run()}>
              Create from starter
            </Button>
          }
        >
          Start from the built-in 15-question baseline (access control, MFA, encryption, vulnerability
          management, incident response, business continuity, data protection and vendor governance), or
          build your own.
        </EmptyState>
      ) : (
        <>
          <div className="mb-3 flex items-center gap-2 text-sm text-slate-600">
            <input
              id={archivedId}
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-cyan-600"
            />
            <label htmlFor={archivedId}>Show archived</label>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            {templates.data.map((t) => (
              <Card key={t.id}>
                <div className="flex items-start justify-between gap-3">
                  <Link href={`/questionnaires/${t.id}`} className="font-semibold text-cyan-700 hover:underline">
                    {t.name}
                  </Link>
                  <StatusBadge status={t.status} />
                </div>
                <p className="mt-2 line-clamp-2 text-sm text-slate-600">{t.description ?? "No description."}</p>
                <p className="mt-3 text-xs text-slate-500">Updated {formatDate(t.updated_at)}</p>
              </Card>
            ))}
          </div>
        </>
      )}
      {creating && <CreateTemplateModal onClose={() => setCreating(false)} />}
    </>
  );
}
