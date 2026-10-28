"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useId, useState } from "react";

import { useApi } from "@/components/providers";
import { OptionModal, QuestionModal } from "@/components/question-modals";
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
  Notice,
  PageHeader,
  StatusBadge,
} from "@/components/ui";
import type { Option, Question, Version } from "@/lib/api/types";
import { formatDate, titleCase } from "@/lib/format";
import { useAction, useQuery } from "@/lib/hooks";
import { validateThresholds } from "@/lib/validation";

function ThresholdsCard({ version, editable, onSaved }: { version: Version; editable: boolean; onSaved: () => void }) {
  const api = useApi();
  const ids = [useId(), useId(), useId()];
  const [values, setValues] = useState([version.low_max, version.medium_max, version.high_max].map(String));
  const [problem, setProblem] = useState<string | null>(null);
  const save = useAction(() =>
    api.updateThresholds(version.id, {
      low_max: Number(values[0]),
      medium_max: Number(values[1]),
      high_max: Number(values[2]),
    }),
  );
  const labels = ["Low below", "Medium below", "High below"];

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const invalid = validateThresholds(Number(values[0]), Number(values[1]), Number(values[2]));
    setProblem(invalid);
    if (invalid) return;
    if (await save.run()) onSaved();
  }

  return (
    <Card title="Risk thresholds">
      <form onSubmit={submit} noValidate>
        <div className="grid grid-cols-3 gap-3">
          {labels.map((label, i) => (
            <Field key={label} label={label} htmlFor={ids[i] ?? label}>
              <input
                id={ids[i]}
                inputMode="numeric"
                className={INPUT}
                disabled={!editable}
                value={values[i]}
                onChange={(e) => setValues(values.map((v, j) => (j === i ? e.target.value : v)))}
              />
            </Field>
          ))}
        </div>
        <p className="mt-3 text-xs text-slate-500">
          A score at or above the last value is Critical. Each band includes its lower bound.
        </p>
        {problem && (
          <p role="alert" className="mt-2 text-sm font-medium text-red-700">
            {problem}
          </p>
        )}
        <div className="mt-3">
          <ActionError error={save.error} />
        </div>
        {editable && (
          <Button type="submit" variant="secondary" loading={save.pending} className="mt-3">
            Save thresholds
          </Button>
        )}
      </form>
    </Card>
  );
}

function QuestionCard({
  question,
  index,
  total,
  editable,
  onMove,
  onEdit,
  onArchive,
  onOption,
  busy,
}: {
  question: Question;
  index: number;
  total: number;
  editable: boolean;
  busy: boolean;
  onMove: (delta: -1 | 1) => void;
  onEdit: () => void;
  onArchive: () => void;
  onOption: (option?: Option) => void;
}) {
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            {index + 1}. {titleCase(question.category)} · weight {question.weight}
          </p>
          <p className="mt-1 font-medium text-slate-900">{question.prompt}</p>
          {question.guidance && <p className="mt-1 text-sm text-slate-600">{question.guidance}</p>}
        </div>
        {editable && (
          <div className="flex items-center gap-1" role="group" aria-label={`Actions for question ${index + 1}`}>
            <Button variant="ghost" disabled={busy || index === 0} onClick={() => onMove(-1)} aria-label={`Move question ${index + 1} up`}>
              ↑
            </Button>
            <Button variant="ghost" disabled={busy || index === total - 1} onClick={() => onMove(1)} aria-label={`Move question ${index + 1} down`}>
              ↓
            </Button>
            <Button variant="ghost" onClick={onEdit} aria-label={`Edit question ${index + 1}`}>
              Edit
            </Button>
            <Button variant="ghost" disabled={busy} onClick={onArchive} aria-label={`Archive question ${index + 1}`}>
              Archive
            </Button>
          </div>
        )}
      </div>
      <ul className="mt-3 space-y-1.5">
        {question.options.map((option) => (
          <li
            key={option.id}
            className={`flex items-center justify-between gap-2 rounded-md bg-slate-50 px-3 py-1.5 text-sm ${option.status === "archived" ? "opacity-60" : ""}`}
          >
            <span>
              {option.label}
              {option.status === "archived" && <span className="ml-2 text-xs text-slate-500">(archived)</span>}
            </span>
            <span className="flex items-center gap-3">
              <span className="font-mono text-xs text-slate-600">
                {option.risk_points === null ? "N/A" : `${option.risk_points} pts`}
              </span>
              {editable && (
                <button
                  type="button"
                  className="text-xs font-medium text-cyan-700 hover:underline"
                  onClick={() => onOption(option)}
                  aria-label={`Edit option ${option.label}`}
                >
                  Edit
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>
      {editable && (
        <button type="button" className="mt-2 text-sm font-medium text-cyan-700 hover:underline" onClick={() => onOption()}>
          + Add option
        </button>
      )}
    </li>
  );
}

export default function VersionPage() {
  const { id, versionId } = useParams<{ id: string; versionId: string }>();
  const api = useApi();
  const router = useRouter();
  const version = useQuery(() => api.getVersion(versionId, true), `version-${versionId}`);
  const template = useQuery(() => api.getTemplate(id), `template-${id}`);
  const [questionModal, setQuestionModal] = useState<{ question?: Question } | null>(null);
  const [optionModal, setOptionModal] = useState<{ question: Question; option?: Option } | null>(null);
  const [confirm, setConfirm] = useState<"publish" | "discard" | null>(null);

  const reload = () => version.reload();
  const act = useAction(async (work: () => Promise<unknown>) => {
    await work();
    reload();
  });
  const publish = useAction(async () => {
    await api.publishVersion(versionId);
    setConfirm(null);
    reload();
    template.reload();
  });
  const discard = useAction(async () => {
    await api.retireVersion(versionId);
    router.push(`/questionnaires/${id}`);
  });
  const clone = useAction(async () => {
    const next = await api.createVersion(id);
    router.push(`/questionnaires/${id}/versions/${next.id}`);
  });

  if (version.error) return <ErrorState error={version.error} onRetry={version.reload} />;
  if (!version.data) return <LoadingState label="Loading version…" />;
  const v = version.data;
  const editable = v.status === "draft";
  const active = v.questions.filter((q) => q.status === "active");
  const archived = v.questions.filter((q) => q.status === "archived");
  const categories = [...new Set(v.questions.map((q) => q.category))];

  function move(index: number, delta: -1 | 1) {
    const ids = active.map((q) => q.id);
    const target = index + delta;
    const a = ids[index];
    const b = ids[target];
    if (a === undefined || b === undefined) return;
    ids[index] = b;
    ids[target] = a;
    void act.run(() => api.reorderQuestions(versionId, ids));
  }

  return (
    <>
      <p className="mb-3 text-sm">
        <Link href={`/questionnaires/${id}`} className="text-cyan-700 hover:underline">
          ← {template.data?.name ?? "Questionnaire"}
        </Link>
      </p>
      <PageHeader
        title={`Version ${v.version_number}`}
        subtitle={
          <span className="inline-flex items-center gap-2">
            <StatusBadge status={v.status} />
            {v.published_at ? `Published ${formatDate(v.published_at)}` : "Not published"} · {active.length} questions
          </span>
        }
        actions={
          editable ? (
            <>
              <Button variant="secondary" onClick={() => setConfirm("discard")}>
                Discard draft
              </Button>
              <Button onClick={() => setConfirm("publish")}>Publish version</Button>
            </>
          ) : (
            <Button loading={clone.pending} disabled={template.data?.status === "archived"} onClick={() => void clone.run()}>
              Create new draft from this version
            </Button>
          )
        }
      />
      <div className="mb-4 space-y-3">
        {!editable && (
          <Notice tone="info">
            This version is {v.status} and cannot be edited. Assessments that use it keep exactly these
            questions, weights and thresholds. To make changes, create a new draft.
          </Notice>
        )}
        <ActionError error={act.error} />
        <ActionError error={clone.error} />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {active.length === 0 ? (
            <EmptyState
              title="No questions yet"
              action={editable ? <Button onClick={() => setQuestionModal({})}>Add a question</Button> : undefined}
            >
              A version needs at least one question, each with two answer options, before it can be published.
            </EmptyState>
          ) : (
            <ol className="space-y-3">
              {active.map((q, i) => (
                <QuestionCard
                  key={q.id}
                  question={q}
                  index={i}
                  total={active.length}
                  editable={editable}
                  busy={act.pending}
                  onMove={(d) => move(i, d)}
                  onEdit={() => setQuestionModal({ question: q })}
                  onArchive={() => void act.run(() => api.updateQuestion(q.id, { status: "archived" }))}
                  onOption={(option) => setOptionModal({ question: q, option })}
                />
              ))}
            </ol>
          )}
          {editable && (
            <Button variant="secondary" onClick={() => setQuestionModal({})}>
              Add question
            </Button>
          )}
          {editable && archived.length > 0 && (
            <Card title={`Archived questions (${archived.length})`}>
              <ul className="divide-y divide-slate-100">
                {archived.map((q) => (
                  <li key={q.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                    <span className="min-w-0 truncate text-slate-600">{q.prompt}</span>
                    <Button variant="ghost" onClick={() => void act.run(() => api.updateQuestion(q.id, { status: "active" }))}>
                      Restore
                    </Button>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
        <div>
          <ThresholdsCard key={`${v.low_max}-${v.medium_max}-${v.high_max}`} version={v} editable={editable} onSaved={reload} />
        </div>
      </div>

      {questionModal && (
        <QuestionModal
          versionId={versionId}
          question={questionModal.question}
          categories={categories}
          onClose={() => setQuestionModal(null)}
          onSaved={() => {
            setQuestionModal(null);
            reload();
          }}
        />
      )}
      {optionModal && (
        <OptionModal
          question={optionModal.question}
          option={optionModal.option}
          onClose={() => setOptionModal(null)}
          onSaved={() => {
            setOptionModal(null);
            reload();
          }}
        />
      )}
      {confirm === "publish" && (
        <Modal title="Publish this version?" onClose={() => setConfirm(null)}>
          <p className="text-sm text-slate-600">
            Publishing freezes the questions, options, weights and thresholds. It cannot be undone; later
            changes need a new draft. Every question needs at least two answer options.
          </p>
          <div className="mt-4">
            <ActionError error={publish.error} />
          </div>
          <div className="mt-6 flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button loading={publish.pending} onClick={() => void publish.run()}>
              Publish
            </Button>
          </div>
        </Modal>
      )}
      {confirm === "discard" && (
        <Modal title="Discard this draft?" onClose={() => setConfirm(null)}>
          <p className="text-sm text-slate-600">
            The draft is retired and cannot be used for assessments. Nothing is deleted.
          </p>
          <div className="mt-4">
            <ActionError error={discard.error} />
          </div>
          <div className="mt-6 flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setConfirm(null)}>
              Keep editing
            </Button>
            <Button variant="danger" loading={discard.pending} onClick={() => void discard.run()}>
              Discard draft
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
