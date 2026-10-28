"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useId, useState } from "react";

import { useApi } from "@/components/providers";
import {
  ActionError,
  Button,
  Card,
  ErrorState,
  Field,
  INPUT,
  LoadingState,
  Modal,
  PageHeader,
  StatusBadge,
} from "@/components/ui";
import type { TemplateDetail } from "@/lib/api/types";
import { formatDate } from "@/lib/format";
import { useAction, useQuery } from "@/lib/hooks";

function EditTemplateModal({
  template,
  onClose,
  onSaved,
}: {
  template: TemplateDetail;
  onClose: () => void;
  onSaved: () => void;
}) {
  const api = useApi();
  const nameId = useId();
  const descriptionId = useId();
  const [name, setName] = useState(template.name);
  const [description, setDescription] = useState(template.description ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const save = useAction(() =>
    api.updateTemplate(template.id, { name: name.trim(), description: description.trim() || null }),
  );
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (name.trim().length === 0) return setProblem("Give the questionnaire a name.");
    setProblem(null);
    if (await save.run()) onSaved();
  }
  return (
    <Modal title="Edit questionnaire" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <Field label="Name" htmlFor={nameId} error={problem ?? undefined}>
          <input id={nameId} className={INPUT} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Description" htmlFor={descriptionId}>
          <textarea id={descriptionId} className={INPUT} rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <div className="mt-4">
          <ActionError error={save.error} />
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={save.pending}>
            Save
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export default function TemplatePage() {
  const { id } = useParams<{ id: string }>();
  const api = useApi();
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const template = useQuery(() => api.getTemplate(id), `template-${id}`);

  const archive = useAction(async (status: "active" | "archived") => {
    await api.updateTemplate(id, { status });
    template.reload();
  });
  const newDraft = useAction(async () => {
    const version = await api.createVersion(id);
    router.push(`/questionnaires/${id}/versions/${version.id}`);
  });

  if (template.error) return <ErrorState error={template.error} onRetry={template.reload} />;
  if (!template.data) return <LoadingState label="Loading questionnaire…" />;
  const t = template.data;
  const draft = t.versions.find((v) => v.status === "draft");

  return (
    <>
      <p className="mb-3 text-sm">
        <Link href="/questionnaires" className="text-cyan-700 hover:underline">
          ← All questionnaires
        </Link>
      </p>
      <PageHeader
        title={t.name}
        subtitle={
          <span className="inline-flex items-center gap-2">
            <StatusBadge status={t.status} /> {t.description ?? "No description."}
          </span>
        }
        actions={
          <>
            <Button variant="secondary" onClick={() => setEditing(true)}>
              Edit details
            </Button>
            <Button
              variant="secondary"
              loading={archive.pending}
              onClick={() => void archive.run(t.status === "active" ? "archived" : "active")}
            >
              {t.status === "active" ? "Archive" : "Restore"}
            </Button>
            {draft ? (
              <Link href={`/questionnaires/${t.id}/versions/${draft.id}`}>
                <Button>Continue editing draft</Button>
              </Link>
            ) : (
              <Button loading={newDraft.pending} disabled={t.status !== "active"} onClick={() => void newDraft.run()}>
                New draft version
              </Button>
            )}
          </>
        }
      />
      <div className="mb-4 space-y-2">
        <ActionError error={archive.error} />
        <ActionError error={newDraft.error} />
      </div>

      <Card title="Versions">
        <p className="mb-4 text-sm text-slate-600">
          A published version is frozen. To change questions, weights or thresholds, start a new draft (a copy
          of the latest published version), edit it, and publish it. Assessments already started keep the
          version they began with.
        </p>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-100 text-sm">
            <caption className="sr-only">Versions</caption>
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th scope="col" className="py-2 pr-4 font-medium">Version</th>
                <th scope="col" className="py-2 pr-4 font-medium">Status</th>
                <th scope="col" className="py-2 pr-4 font-medium">Questions</th>
                <th scope="col" className="py-2 pr-4 font-medium">Published</th>
                <th scope="col" className="py-2 font-medium">
                  <span className="sr-only">Open</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {t.versions.map((v) => (
                <tr key={v.id}>
                  <td className="py-2.5 pr-4 font-medium">v{v.version_number}</td>
                  <td className="py-2.5 pr-4"><StatusBadge status={v.status} /></td>
                  <td className="py-2.5 pr-4 tabular-nums">{v.question_count}</td>
                  <td className="py-2.5 pr-4 text-slate-600">{formatDate(v.published_at)}</td>
                  <td className="py-2.5 text-right">
                    <Link
                      href={`/questionnaires/${t.id}/versions/${v.id}`}
                      className="font-medium text-cyan-700 hover:underline"
                    >
                      {v.status === "draft" ? "Edit" : "View"}
                      <span className="sr-only"> version {v.version_number}</span>
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {editing && (
        <EditTemplateModal
          template={t}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            template.reload();
          }}
        />
      )}
    </>
  );
}
