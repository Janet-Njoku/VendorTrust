"use client";

import { useState } from "react";

import type { AssessmentDetail, AssessmentQuestion } from "@/lib/api/types";
import { describeKind, toApiError } from "@/lib/api/errors";
import { titleCase } from "@/lib/format";
import { useAction } from "@/lib/hooks";

import { useApi } from "./providers";
import { ActionError, Button, Card, INPUT, Notice, ProgressBar } from "./ui";

type Saved = { optionId: string; comment: string };
type SaveState = "saving" | "saved" | "error";

/**
 * Answer the questions of an in-progress assessment. Every choice is saved immediately (and a note
 * when you leave its field), so nothing is lost; "Finalize and score" is only enabled when every
 * question has an answer, and the server re-checks that.
 */
export function AnswerForm({ detail, onScored }: { detail: AssessmentDetail; onScored: () => void }) {
  const api = useApi();
  const [answers, setAnswers] = useState<Record<string, Saved>>(() =>
    Object.fromEntries(
      detail.questions
        .filter((q) => q.response)
        .map((q) => [q.id, { optionId: q.response?.option_id ?? "", comment: q.response?.comment ?? "" }]),
    ),
  );
  const [state, setState] = useState<Record<string, SaveState>>({});
  const [messages, setMessages] = useState<Record<string, string>>({});
  const [lastSaved, setLastSaved] = useState<Record<string, Saved>>(answers);

  const answered = detail.questions.filter((q) => answers[q.id]).length;
  const total = detail.questions.length;

  async function save(question: AssessmentQuestion, next: Saved) {
    setState((s) => ({ ...s, [question.id]: "saving" }));
    setMessages((m) => ({ ...m, [question.id]: "" }));
    try {
      await api.setResponse(detail.id, question.id, {
        option_id: next.optionId,
        comment: next.comment.trim() || null,
      });
      setLastSaved((s) => ({ ...s, [question.id]: next }));
      setState((s) => ({ ...s, [question.id]: "saved" }));
    } catch (error) {
      setState((s) => ({ ...s, [question.id]: "error" }));
      setMessages((m) => ({ ...m, [question.id]: describeKind(toApiError(error)) }));
    }
  }

  function choose(question: AssessmentQuestion, optionId: string) {
    const next = { optionId, comment: answers[question.id]?.comment ?? "" };
    setAnswers((a) => ({ ...a, [question.id]: next }));
    void save(question, next);
  }

  function noteChanged(question: AssessmentQuestion, comment: string) {
    const current = answers[question.id];
    if (current) setAnswers((a) => ({ ...a, [question.id]: { ...current, comment } }));
    else setAnswers((a) => ({ ...a, [question.id]: { optionId: "", comment } }));
  }

  function noteBlur(question: AssessmentQuestion) {
    const current = answers[question.id];
    if (!current?.optionId) return;
    if (current.comment !== lastSaved[question.id]?.comment) void save(question, current);
  }

  const finalize = useAction(async () => {
    await api.scoreAssessment(detail.id);
    onScored();
  });

  const categories = [...new Set(detail.questions.map((q) => q.category))];
  const unanswered = finalize.error?.unanswered ?? [];

  return (
    <div className="space-y-6">
      <Card>
        <div className="mb-2 flex items-center justify-between text-sm">
          <span className="font-medium text-slate-800">Progress</span>
          <span className="tabular-nums text-slate-600">
            {answered} of {total} answered
          </span>
        </div>
        <ProgressBar value={answered} max={total} label="Questions answered" />
      </Card>

      {categories.map((category) => (
        <Card key={category} title={titleCase(category)}>
          <div className="space-y-6">
            {detail.questions
              .filter((q) => q.category === category)
              .map((q) => {
                const current = answers[q.id];
                const flagged = unanswered.includes(q.id) && !current?.optionId;
                return (
                  <fieldset key={q.id} className={flagged ? "rounded-lg ring-2 ring-red-300 p-3" : undefined}>
                    <legend className="text-sm font-medium text-slate-900">
                      {q.prompt} <span className="ml-1 text-xs font-normal text-slate-500">weight {q.weight}</span>
                    </legend>
                    {q.guidance && <p className="mt-1 text-xs text-slate-500">{q.guidance}</p>}
                    <div className="mt-2 space-y-1.5">
                      {q.options.map((option) => (
                        <label
                          key={option.id}
                          className={`flex cursor-pointer items-center gap-2.5 rounded-md border px-3 py-2 text-sm ${
                            current?.optionId === option.id
                              ? "border-cyan-600 bg-cyan-50"
                              : "border-slate-200 hover:bg-slate-50"
                          }`}
                        >
                          <input
                            type="radio"
                            name={`q-${q.id}`}
                            value={option.id}
                            checked={current?.optionId === option.id}
                            onChange={() => choose(q, option.id)}
                            className="h-4 w-4 text-cyan-600"
                          />
                          {option.label}
                        </label>
                      ))}
                    </div>
                    <textarea
                      aria-label={`Note for: ${q.prompt}`}
                      className={`${INPUT} mt-2`}
                      rows={2}
                      placeholder="Optional note or evidence reference"
                      value={current?.comment ?? ""}
                      onChange={(e) => noteChanged(q, e.target.value)}
                      onBlur={() => noteBlur(q)}
                    />
                    <p aria-live="polite" className="mt-1 h-4 text-xs">
                      {state[q.id] === "saving" && <span className="text-slate-500">Saving…</span>}
                      {state[q.id] === "saved" && <span className="text-emerald-700">Saved</span>}
                      {state[q.id] === "error" && <span className="font-medium text-red-700">{messages[q.id]}</span>}
                    </p>
                  </fieldset>
                );
              })}
          </div>
        </Card>
      ))}

      <Card title="Finalize">
        <p className="text-sm text-slate-600">
          Scoring locks the assessment: answers can no longer change, and the result is stored with the
          questionnaire version it used. Start a new assessment to reassess a vendor.
        </p>
        {finalize.error && unanswered.length > 0 ? (
          <div className="mt-4">
            <Notice tone="error">
              {unanswered.length} question{unanswered.length === 1 ? " still needs" : "s still need"} an answer. They are
              highlighted above.
            </Notice>
          </div>
        ) : (
          <div className="mt-4">
            <ActionError error={finalize.error} />
          </div>
        )}
        <div className="mt-4 flex items-center gap-3">
          <Button loading={finalize.pending} disabled={answered < total} onClick={() => void finalize.run()}>
            Finalize and score
          </Button>
          {answered < total && <span className="text-sm text-slate-500">Answer every question to enable scoring.</span>}
        </div>
      </Card>
    </div>
  );
}
