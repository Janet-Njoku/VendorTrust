/** Client-side checks that mirror the API's rules, for immediate feedback. The API stays the authority. */

import type { OptionInput, QuestionInput } from "./api/types";

export interface OptionForm {
  label: string;
  points: string;
  notApplicable: boolean;
}

export interface QuestionForm {
  category: string;
  prompt: string;
  guidance: string;
  weight: string;
  options: OptionForm[];
}

export type FormErrors = Record<string, string>;

const SLUG = /^[a-z][a-z0-9_]{1,59}$/;
const CONTROL = /[\u0000-\u0008\u000b-\u001f\u007f]/;

export function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 60);
}

export function emptyOption(): OptionForm {
  return { label: "", points: "", notApplicable: false };
}

export function emptyQuestionForm(category = ""): QuestionForm {
  return {
    category,
    prompt: "",
    guidance: "",
    weight: "3",
    options: [
      { label: "Yes", points: "0", notApplicable: false },
      { label: "No", points: "100", notApplicable: false },
    ],
  };
}

export function validateOption(option: OptionForm): string | null {
  const label = option.label.trim();
  if (label.length === 0) return "Give every option a label.";
  if (label.length > 200) return "Option labels must be 200 characters or fewer.";
  if (option.notApplicable) return null;
  if (!/^\d{1,3}$/.test(option.points.trim())) return "Risk points must be a whole number from 0 to 100.";
  const points = Number(option.points);
  if (points < 0 || points > 100) return "Risk points must be between 0 and 100.";
  return null;
}

export function toOptionInput(option: OptionForm): OptionInput {
  return {
    label: option.label.trim(),
    risk_points: option.notApplicable ? null : Number(option.points),
  };
}

export function validateQuestionForm(form: QuestionForm, options = true): FormErrors {
  const errors: FormErrors = {};
  if (!SLUG.test(form.category.trim())) {
    errors.category = "Use 2 to 60 lowercase letters, digits or underscores, starting with a letter.";
  }
  const prompt = form.prompt.trim();
  if (prompt.length < 5) errors.prompt = "The question must be at least 5 characters.";
  else if (prompt.length > 1000) errors.prompt = "The question must be 1000 characters or fewer.";
  else if (CONTROL.test(prompt)) errors.prompt = "The question must not contain control characters.";
  if (form.guidance.length > 2000) errors.guidance = "Guidance must be 2000 characters or fewer.";
  if (!/^\d{1,2}$/.test(form.weight.trim()) || Number(form.weight) < 1 || Number(form.weight) > 10) {
    errors.weight = "Weight must be a whole number from 1 to 10.";
  }
  if (options) {
    if (form.options.length < 2) errors.options = "A question needs at least two options.";
    else if (form.options.length > 12) errors.options = "A question can have at most 12 options.";
    else {
      const first = form.options.map(validateOption).find((m) => m !== null);
      if (first) errors.options = first;
    }
  }
  return errors;
}

export function toQuestionInput(form: QuestionForm): QuestionInput {
  return {
    category: form.category.trim(),
    prompt: form.prompt.trim(),
    guidance: form.guidance.trim() || null,
    weight: Number(form.weight),
    options: form.options.map(toOptionInput),
  };
}

export function validateThresholds(low: number, medium: number, high: number): string | null {
  if (![low, medium, high].every(Number.isInteger)) return "Thresholds must be whole numbers.";
  if (!(0 < low && low < medium && medium < high && high <= 100)) {
    return "Thresholds must satisfy 0 < low < medium < high ≤ 100.";
  }
  return null;
}
