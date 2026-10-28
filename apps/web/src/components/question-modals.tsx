"use client";

import { useId, useState } from "react";

import type { Option, Question } from "@/lib/api/types";
import { useAction } from "@/lib/hooks";
import {
  type FormErrors,
  type OptionForm,
  type QuestionForm,
  emptyOption,
  emptyQuestionForm,
  slugify,
  toOptionInput,
  toQuestionInput,
  validateOption,
  validateQuestionForm,
} from "@/lib/validation";

import { useApi } from "./providers";
import { ActionError, Button, Field, INPUT, Modal } from "./ui";

function OptionRow({
  option,
  index,
  onChange,
  onRemove,
  removable,
}: {
  option: OptionForm;
  index: number;
  onChange: (next: OptionForm) => void;
  onRemove?: () => void;
  removable: boolean;
}) {
  const n = index + 1;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input
        aria-label={`Option ${n} label`}
        className={`${INPUT} min-w-0 flex-1`}
        placeholder="Answer label"
        value={option.label}
        onChange={(e) => onChange({ ...option, label: e.target.value })}
      />
      <input
        aria-label={`Option ${n} risk points`}
        className={`${INPUT} w-20`}
        inputMode="numeric"
        placeholder="0-100"
        disabled={option.notApplicable}
        value={option.notApplicable ? "" : option.points}
        onChange={(e) => onChange({ ...option, points: e.target.value })}
      />
      <label className="flex items-center gap-1.5 text-xs text-slate-600">
        <input
          type="checkbox"
          checked={option.notApplicable}
          onChange={(e) => onChange({ ...option, notApplicable: e.target.checked })}
          aria-label={`Option ${n} not applicable`}
        />
        N/A
      </label>
      {removable && onRemove && (
        <button type="button" onClick={onRemove} className="text-xs font-medium text-red-700 hover:underline" aria-label={`Remove option ${n}`}>
          Remove
        </button>
      )}
    </div>
  );
}

/** Add a question with its options to a draft version, or edit the fields of an existing one. */
export function QuestionModal({
  versionId,
  question,
  categories,
  onClose,
  onSaved,
}: {
  versionId: string;
  question?: Question;
  categories: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const api = useApi();
  const ids = { category: useId(), prompt: useId(), guidance: useId(), weight: useId(), list: useId() };
  const [form, setForm] = useState<QuestionForm>(
    question
      ? {
          category: question.category,
          prompt: question.prompt,
          guidance: question.guidance ?? "",
          weight: String(question.weight),
          options: [],
        }
      : emptyQuestionForm(categories[0] ?? ""),
  );
  const [errors, setErrors] = useState<FormErrors>({});
  const save = useAction(async () => {
    if (question) {
      const input = toQuestionInput({ ...form, options: [emptyOption(), emptyOption()] });
      return api.updateQuestion(question.id, {
        category: input.category,
        prompt: input.prompt,
        guidance: input.guidance,
        weight: input.weight,
      });
    }
    return api.addQuestion(versionId, toQuestionInput(form));
  });

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const found = validateQuestionForm(form, !question);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    if (await save.run()) onSaved();
  }

  const serverErrors = save.error?.fieldErrors ?? {};
  const error = (name: string) => errors[name] ?? serverErrors[name];

  return (
    <Modal title={question ? "Edit question" : "Add question"} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <Field label="Category" htmlFor={ids.category} error={error("category")} hint="A short slug such as access_control or mfa.">
          <input
            id={ids.category}
            className={INPUT}
            list={ids.list}
            value={form.category}
            onChange={(e) => setForm({ ...form, category: e.target.value })}
            onBlur={() => form.category && setForm((f) => ({ ...f, category: slugify(f.category) }))}
          />
          <datalist id={ids.list}>
            {categories.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
        </Field>
        <Field label="Question" htmlFor={ids.prompt} error={error("prompt")}>
          <textarea id={ids.prompt} rows={3} className={INPUT} value={form.prompt} onChange={(e) => setForm({ ...form, prompt: e.target.value })} />
        </Field>
        <Field label="Guidance (optional)" htmlFor={ids.guidance} error={error("guidance")}>
          <textarea id={ids.guidance} rows={2} className={INPUT} value={form.guidance} onChange={(e) => setForm({ ...form, guidance: e.target.value })} />
        </Field>
        <Field label="Weight (1 to 10)" htmlFor={ids.weight} error={error("weight")} hint="How much this question counts toward the overall score.">
          <input id={ids.weight} inputMode="numeric" className={`${INPUT} w-24`} value={form.weight} onChange={(e) => setForm({ ...form, weight: e.target.value })} />
        </Field>

        {!question && (
          <fieldset className="mt-4">
            <legend className="mb-1 text-sm font-medium text-slate-700">Answer options</legend>
            <p className="mb-2 text-xs text-slate-500">
              Risk points run from 0 (best answer) to 100 (worst). Tick N/A to leave an answer out of the score.
            </p>
            <div className="space-y-2">
              {form.options.map((option, index) => (
                <OptionRow
                  key={index}
                  option={option}
                  index={index}
                  removable={form.options.length > 2}
                  onChange={(next) =>
                    setForm({ ...form, options: form.options.map((o, i) => (i === index ? next : o)) })
                  }
                  onRemove={() => setForm({ ...form, options: form.options.filter((_, i) => i !== index) })}
                />
              ))}
            </div>
            {form.options.length < 12 && (
              <button
                type="button"
                className="mt-2 text-sm font-medium text-cyan-700 hover:underline"
                onClick={() => setForm({ ...form, options: [...form.options, emptyOption()] })}
              >
                + Add option
              </button>
            )}
            {error("options") && (
              <p role="alert" className="mt-1 text-xs font-medium text-red-700">
                {error("options")}
              </p>
            )}
          </fieldset>
        )}

        <div className="mt-4">
          <ActionError error={save.error && Object.keys(serverErrors).length === 0 ? save.error : undefined} />
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={save.pending}>
            {question ? "Save question" : "Add question"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/** Add or edit one answer option of a question in a draft version. */
export function OptionModal({
  question,
  option,
  onClose,
  onSaved,
}: {
  question: Question;
  option?: Option;
  onClose: () => void;
  onSaved: () => void;
}) {
  const api = useApi();
  const [form, setForm] = useState<OptionForm>(
    option
      ? {
          label: option.label,
          points: option.risk_points === null ? "" : String(option.risk_points),
          notApplicable: option.risk_points === null,
        }
      : emptyOption(),
  );
  const [problem, setProblem] = useState<string | null>(null);
  const save = useAction(async () => {
    const input = toOptionInput(form);
    return option ? api.updateOption(option.id, input) : api.addOption(question.id, input);
  });

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const invalid = validateOption(form);
    setProblem(invalid);
    if (invalid) return;
    if (await save.run()) onSaved();
  }

  return (
    <Modal title={option ? "Edit option" : "Add option"} onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <p className="mb-3 text-sm text-slate-600">{question.prompt}</p>
        <OptionRow option={form} index={0} removable={false} onChange={setForm} />
        {problem && (
          <p role="alert" className="mt-2 text-xs font-medium text-red-700">
            {problem}
          </p>
        )}
        <div className="mt-4">
          <ActionError error={save.error} />
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={save.pending}>
            {option ? "Save option" : "Add option"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
