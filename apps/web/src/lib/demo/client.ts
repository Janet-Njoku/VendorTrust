/**
 * DEMO MODE ONLY. An in-memory stand-in for the VendorTrust API with sample data, used when
 * NEXT_PUBLIC_DEMO_MODE=true (and by the UI tests). It implements the same `ApiClient` interface and
 * mirrors the backend's workflow rules and messages, but it never makes a network request, never
 * signs anyone in for real, and every screen shows a banner while it is active. It is not wired into
 * any production path: the real client is used unless demo mode is switched on explicitly.
 */

import type { ApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import type {
  Assessment,
  AssessmentDetail,
  AssessmentResponse,
  AssessmentResult,
  Breakdown,
  CategoryScore,
  Me,
  Option,
  Page,
  Question,
  RiskLevel,
  Template,
  TemplateDetail,
  Thresholds,
  Version,
  Vendor,
} from "../api/types";
import { STARTER_DESCRIPTION, STARTER_NAME, STARTER_QUESTIONS } from "./starter";

const DEFAULT_THRESHOLDS: Thresholds = { low_max: 25, medium_max: 50, high_max: 75 };
const ORG_ID = "00000000-0000-4000-8000-0000000000a1";
const USER_ID = "00000000-0000-4000-8000-0000000000b1";

let counter = 0;
function uid(): string {
  counter += 1;
  const tail = counter.toString(16).padStart(12, "0");
  return `00000000-0000-4000-8000-${tail}`;
}

function invalid(field: string, message: string): ApiError {
  return new ApiError(422, "Please correct the highlighted fields.", { fieldErrors: { [field]: message } });
}

function checkName(name: unknown, field = "name"): string {
  const value = typeof name === "string" ? name.trim() : "";
  if (value.length < 1) throw invalid(field, "String should have at least 1 character");
  if (value.length > 200) throw invalid(field, "String should have at most 200 characters");
  return value;
}

/** Score arithmetic identical to the backend: weighted average, rounded half-up to one decimal. */
export function weightedScore(items: { weight: number; risk: number | null }[]): number | null {
  const applicable = items.filter((i) => i.risk !== null);
  const weight = applicable.reduce((s, i) => s + i.weight, 0);
  if (weight === 0) return null;
  const numerator = applicable.reduce((s, i) => s + i.weight * (i.risk ?? 0), 0) * 10;
  return Math.floor((2 * numerator + weight) / (2 * weight)) / 10;
}

export function classifyScore(score: number, t: Thresholds): RiskLevel {
  if (score < t.low_max) return "low";
  if (score < t.medium_max) return "medium";
  if (score < t.high_max) return "high";
  return "critical";
}

type SyncApi = {
  [K in keyof ApiClient]: ApiClient[K] extends (...args: infer A) => Promise<infer R>
    ? (...args: A) => R
    : never;
};

interface Store {
  vendors: Vendor[];
  templates: Template[];
  versions: Version[];
  assessments: Assessment[];
  responses: AssessmentResponse[];
}

export interface DemoOptions {
  /** Artificial latency so loading states are visible in a demo. Zero in tests. */
  delayMs?: number;
  /** Start with sample vendors, a questionnaire and assessments (default true). */
  seed?: boolean;
}

export function createDemoClient(options: DemoOptions = {}): ApiClient {
  const delay = options.delayMs ?? 0;
  const store: Store = { vendors: [], templates: [], versions: [], assessments: [], responses: [] };
  const now = () => new Date().toISOString();

  const wait = async () => {
    if (delay > 0) await new Promise((r) => setTimeout(r, delay));
  };
  const copy = <T,>(value: T): T => structuredClone(value);

  const findVendor = (id: string) => {
    const v = store.vendors.find((x) => x.id === id);
    if (!v) throw new ApiError(404, "Vendor not found");
    return v;
  };
  const findTemplate = (id: string) => {
    const t = store.templates.find((x) => x.id === id);
    if (!t) throw new ApiError(404, "Questionnaire not found");
    return t;
  };
  const findVersion = (id: string) => {
    const v = store.versions.find((x) => x.id === id);
    if (!v) throw new ApiError(404, "Questionnaire version not found");
    return v;
  };
  const findQuestion = (id: string) => {
    for (const version of store.versions) {
      const question = version.questions.find((q) => q.id === id);
      if (question) return { version, question };
    }
    throw new ApiError(404, "Question not found");
  };
  const findOption = (id: string) => {
    for (const version of store.versions) {
      for (const question of version.questions) {
        const option = question.options.find((o) => o.id === id);
        if (option) return { version, question, option };
      }
    }
    throw new ApiError(404, "Option not found");
  };
  const findAssessment = (id: string) => {
    const a = store.assessments.find((x) => x.id === id);
    if (!a) throw new ApiError(404, "Assessment not found");
    return a;
  };
  const requireDraft = (version: Version) => {
    if (version.status !== "draft") {
      throw new ApiError(409, "Only a draft questionnaire version can be edited");
    }
  };
  const touch = <T extends { updated_at: string }>(row: T) => {
    row.updated_at = now();
  };
  const activeQuestions = (version: Version) =>
    version.questions.filter((q) => q.status === "active").sort((a, b) => a.position - b.position);

  function summary(template: Template): TemplateDetail {
    const versions = store.versions
      .filter((v) => v.template_id === template.id)
      .sort((a, b) => b.version_number - a.version_number)
      .map((v) => ({
        id: v.id,
        version_number: v.version_number,
        status: v.status,
        question_count: activeQuestions(v).length,
        published_at: v.published_at,
      }));
    return { ...copy(template), versions };
  }

  function validateQuestion(body: {
    category?: string;
    prompt?: string;
    weight?: number;
    options?: { label: string; risk_points: number | null }[];
  }) {
    if (body.category !== undefined && !/^[a-z][a-z0-9_]{1,59}$/.test(body.category)) {
      throw invalid("category", "String should match pattern '^[a-z][a-z0-9_]{1,59}$'");
    }
    if (body.prompt !== undefined && (body.prompt.trim().length < 5 || body.prompt.length > 1000)) {
      throw invalid("prompt", "String should have at least 5 characters");
    }
    if (body.weight !== undefined && (!Number.isInteger(body.weight) || body.weight < 1 || body.weight > 10)) {
      throw invalid("weight", "Input should be between 1 and 10");
    }
    if (body.options !== undefined) {
      if (body.options.length < 2) throw invalid("options", "List should have at least 2 items");
      for (const o of body.options) {
        if (o.risk_points !== null && (o.risk_points < 0 || o.risk_points > 100)) {
          throw invalid("options", "Risk points must be between 0 and 100");
        }
      }
    }
  }

  function newVersion(template: Template, number: number, from?: Version): Version {
    const version: Version = {
      id: uid(),
      org_id: ORG_ID,
      template_id: template.id,
      version_number: number,
      status: "draft",
      published_at: null,
      created_at: now(),
      updated_at: now(),
      ...(from
        ? { low_max: from.low_max, medium_max: from.medium_max, high_max: from.high_max }
        : DEFAULT_THRESHOLDS),
      questions: [],
    };
    store.versions.push(version);
    return version;
  }

  function appendQuestion(
    version: Version,
    body: { category: string; prompt: string; guidance?: string | null; weight: number; options: { label: string; risk_points: number | null }[] },
  ): Question {
    const position = Math.max(0, ...version.questions.map((q) => q.position)) + 1;
    const question: Question = {
      id: uid(),
      position,
      category: body.category,
      prompt: body.prompt.trim(),
      guidance: body.guidance ?? null,
      weight: body.weight,
      status: "active",
      options: body.options.map((o, i) => ({
        id: uid(),
        position: i + 1,
        label: o.label.trim(),
        risk_points: o.risk_points,
        status: "active",
      })),
    };
    version.questions.push(question);
    return question;
  }

  function publish(version: Version) {
    const questions = activeQuestions(version);
    const ready =
      questions.length > 0 &&
      questions.every((q) => q.options.filter((o) => o.status === "active").length >= 2);
    if (!ready) {
      throw new ApiError(
        409,
        "The questionnaire is not ready to publish: it needs at least one question, and every question needs at least two options",
      );
    }
    version.status = "published";
    version.published_at = now();
    touch(version);
  }

  function loadDetail(a: Assessment): AssessmentDetail {
    const version = findVersion(a.version_id);
    const questions = activeQuestions(version).map((q) => ({
      ...copy(q),
      options: copy(q.options.filter((o) => o.status === "active")),
      response: copy(
        store.responses.find((r) => r.assessment_id === a.id && r.question_id === q.id) ?? null,
      ),
    }));
    return {
      ...copy(a),
      progress: { answered: questions.filter((q) => q.response).length, total: questions.length },
      questions,
    };
  }

  function resultOf(a: Assessment): AssessmentResult {
    if (a.status !== "scored" || a.score === null || !a.risk_level || !a.scored_at || !a.scored_by) {
      throw new ApiError(409, "The assessment has not been scored yet");
    }
    const breakdown = breakdowns.get(a.id);
    if (!breakdown) throw new ApiError(409, "The assessment has not been scored yet");
    return {
      assessment_id: a.id,
      vendor_id: a.vendor_id,
      version_id: a.version_id,
      score: a.score,
      risk_level: a.risk_level,
      scored_at: a.scored_at,
      scored_by: a.scored_by,
      breakdown: copy(breakdown),
    };
  }
  const breakdowns = new Map<string, Breakdown>();

  function score(a: Assessment): AssessmentResult {
    if (a.status === "scored") throw new ApiError(409, "The assessment has already been scored");
    const version = findVersion(a.version_id);
    const rows = activeQuestions(version).map((q) => {
      const response = store.responses.find((r) => r.assessment_id === a.id && r.question_id === q.id);
      const option = response ? q.options.find((o) => o.id === response.option_id) : undefined;
      return { q, answered: Boolean(response), risk: option?.risk_points ?? null };
    });
    const unanswered = rows.filter((r) => !r.answered).map((r) => r.q.id);
    if (unanswered.length > 0) {
      throw new ApiError(409, "Every question must be answered before the assessment can be scored", {
        unanswered,
      });
    }
    const overall = weightedScore(rows.map((r) => ({ weight: r.q.weight, risk: r.risk })));
    if (overall === null) {
      throw new ApiError(409, "Every question is not applicable, so there is nothing to score");
    }
    const thresholds = { low_max: version.low_max, medium_max: version.medium_max, high_max: version.high_max };
    const level = classifyScore(overall, thresholds);
    const categories: Record<string, CategoryScore> = {};
    for (const category of [...new Set(rows.map((r) => r.q.category))].sort()) {
      const inCategory = rows.filter((r) => r.q.category === category);
      const applicable = inCategory.filter((r) => r.risk !== null);
      categories[category] = {
        score: weightedScore(inCategory.map((r) => ({ weight: r.q.weight, risk: r.risk }))),
        weight: applicable.reduce((s, r) => s + r.q.weight, 0),
        questions: inCategory.length,
      };
    }
    const breakdown: Breakdown = {
      method: "weighted_average_risk_v1",
      score: overall,
      risk_level: level,
      thresholds,
      applicable_weight: rows.filter((r) => r.risk !== null).reduce((s, r) => s + r.q.weight, 0),
      categories,
      not_applicable: rows.filter((r) => r.risk === null).map((r) => r.q.id),
      items: rows.map((r) => ({
        question_id: r.q.id,
        category: r.q.category,
        weight: r.q.weight,
        risk_points: r.risk,
      })),
    };
    breakdowns.set(a.id, breakdown);
    a.status = "scored";
    a.score = overall;
    a.risk_level = level;
    a.scored_at = now();
    a.scored_by = USER_ID;
    touch(a);
    return resultOf(a);
  }

  const ops: SyncApi = {
    getMe() {
      const active = { org_id: ORG_ID, role: "org_admin" as const, vendor_id: null };
      const me: Me = {
        user_id: USER_ID,
        email: "demo.analyst@example.invalid",
        status: "active",
        memberships: [active],
        active,
        has_access: true,
        needs_organization: false,
        mfa: { required: false, satisfied: true, level: "aal2" },
      };
      return me;
    },
    listVendors(p) {
      const limit = p?.limit ?? 50;
      const offset = p?.offset ?? 0;
      const rows = store.vendors
        .filter((v) => !p?.status || v.status === p.status)
        .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
      return { items: copy(rows.slice(offset, offset + limit)), limit, offset };
    },
    getVendor(id) {
      return copy(findVendor(id));
    },
    createVendor(body) {
      const vendor: Vendor = {
        id: uid(),
        org_id: ORG_ID,
        name: checkName(body.name),
        status: body.status ?? "active",
        created_at: now(),
        updated_at: now(),
      };
      store.vendors.push(vendor);
      return copy(vendor);
    },
    updateVendor(id, body) {
      const vendor = findVendor(id);
      if (body.name !== undefined) vendor.name = checkName(body.name);
      if (body.status !== undefined) vendor.status = body.status;
      touch(vendor);
      return copy(vendor);
    },

    listTemplates(p) {
      return copy(
        store.templates
          .filter((t) => p?.includeArchived || t.status === "active")
          .sort((a, b) => a.name.localeCompare(b.name)),
      );
    },
    getTemplate(id) {
      return summary(findTemplate(id));
    },
    createTemplate(body) {
      const template: Template = {
        id: uid(),
        org_id: ORG_ID,
        name: checkName(body.name),
        description: body.description?.trim() || null,
        status: "active",
        created_at: now(),
        updated_at: now(),
      };
      store.templates.push(template);
      newVersion(template, 1);
      return summary(template);
    },
    createFromStarter(body) {
      const template: Template = {
        id: uid(),
        org_id: ORG_ID,
        name: body?.name ? checkName(body.name) : STARTER_NAME,
        description: STARTER_DESCRIPTION,
        status: "active",
        created_at: now(),
        updated_at: now(),
      };
      store.templates.push(template);
      const version = newVersion(template, 1);
      for (const q of STARTER_QUESTIONS) appendQuestion(version, q);
      return summary(template);
    },
    updateTemplate(id, body) {
      const template = findTemplate(id);
      if (body.name !== undefined) template.name = checkName(body.name);
      if (body.description !== undefined) template.description = body.description?.trim() || null;
      if (body.status !== undefined) template.status = body.status;
      touch(template);
      return summary(template);
    },
    createVersion(templateId) {
      const template = findTemplate(templateId);
      if (template.status !== "active") throw new ApiError(409, "The questionnaire is archived");
      const versions = store.versions.filter((v) => v.template_id === templateId);
      if (versions.some((v) => v.status === "draft")) {
        throw new ApiError(409, "A draft version already exists; edit or retire it first");
      }
      const source = [...versions].sort((a, b) => b.version_number - a.version_number)[0];
      const next = newVersion(template, (source?.version_number ?? 0) + 1, source);
      for (const q of source ? activeQuestions(source) : []) {
        appendQuestion(next, {
          category: q.category,
          prompt: q.prompt,
          guidance: q.guidance,
          weight: q.weight,
          options: q.options.filter((o) => o.status === "active"),
        });
      }
      return copy(next);
    },

    getVersion(id, includeArchived) {
      const version = copy(findVersion(id));
      version.questions = version.questions
        .filter((q) => includeArchived || q.status === "active")
        .map((q) => ({ ...q, options: q.options.filter((o) => includeArchived || o.status === "active") }))
        .sort((a, b) => a.position - b.position);
      return version;
    },
    updateThresholds(id, body) {
      const version = findVersion(id);
      requireDraft(version);
      if (!(0 < body.low_max && body.low_max < body.medium_max && body.medium_max < body.high_max && body.high_max <= 100)) {
        throw invalid("form", "Thresholds must satisfy 0 < low < medium < high <= 100");
      }
      Object.assign(version, body);
      touch(version);
      return copy(version);
    },
    publishVersion(id) {
      const version = findVersion(id);
      if (version.status !== "draft") throw new ApiError(409, `A ${version.status} version cannot be published`);
      publish(version);
      return copy(version);
    },
    retireVersion(id) {
      const version = findVersion(id);
      if (version.status === "retired") throw new ApiError(409, "A retired version cannot be retired");
      version.status = "retired";
      touch(version);
      return copy(version);
    },
    reorderQuestions(versionId, ids) {
      const version = findVersion(versionId);
      requireDraft(version);
      const current = activeQuestions(version).map((q) => q.id);
      if (current.length !== ids.length || new Set(ids).size !== ids.length || !ids.every((i) => current.includes(i))) {
        throw new ApiError(422, "question_ids must list every active question exactly once");
      }
      ids.forEach((qid, index) => {
        const q = version.questions.find((x) => x.id === qid);
        if (q) q.position = index + 1;
      });
      return ops.getVersion(versionId);
    },
    addQuestion(versionId, body) {
      const version = findVersion(versionId);
      requireDraft(version);
      validateQuestion(body);
      return copy(appendQuestion(version, body));
    },
    updateQuestion(id, body) {
      const { version, question } = findQuestion(id);
      requireDraft(version);
      validateQuestion(body);
      if (body.category !== undefined) question.category = body.category;
      if (body.prompt !== undefined) question.prompt = body.prompt.trim();
      if (body.guidance !== undefined) question.guidance = body.guidance;
      if (body.weight !== undefined) question.weight = body.weight;
      if (body.status !== undefined) question.status = body.status;
      return copy(question);
    },
    addOption(questionId, body) {
      const { version, question } = findQuestion(questionId);
      requireDraft(version);
      validateQuestion({ options: [body, body] });
      const option: Option = {
        id: uid(),
        position: Math.max(0, ...question.options.map((o) => o.position)) + 1,
        label: checkName(body.label, "label"),
        risk_points: body.risk_points,
        status: "active",
      };
      question.options.push(option);
      return copy(option);
    },
    updateOption(id, body) {
      const { version, option } = findOption(id);
      requireDraft(version);
      if (body.label !== undefined) option.label = checkName(body.label, "label");
      if (body.risk_points !== undefined) {
        if (body.risk_points !== null && (body.risk_points < 0 || body.risk_points > 100)) {
          throw invalid("risk_points", "Input should be between 0 and 100");
        }
        option.risk_points = body.risk_points;
      }
      if (body.status !== undefined) option.status = body.status;
      return copy(option);
    },

    createAssessment(vendorId, body) {
      const vendor = findVendor(vendorId);
      let version: Version | undefined;
      if ("version_id" in body) {
        version = findVersion(body.version_id);
      } else {
        version = store.versions
          .filter((v) => v.template_id === body.questionnaire_id && v.status === "published")
          .sort((a, b) => b.version_number - a.version_number)[0];
        if (!version) throw new ApiError(409, "The questionnaire has no published version available");
      }
      const template = findTemplate(version.template_id);
      if (version.status !== "published" || template.status !== "active" || vendor.status !== "active") {
        throw new ApiError(
          409,
          "This assessment cannot be started: the questionnaire version must be published, its questionnaire active, and the vendor active",
        );
      }
      const assessment: Assessment = {
        id: uid(),
        org_id: ORG_ID,
        vendor_id: vendorId,
        version_id: version.id,
        status: "in_progress",
        created_by: USER_ID,
        score: null,
        risk_level: null,
        scored_at: null,
        scored_by: null,
        created_at: now(),
        updated_at: now(),
      };
      store.assessments.push(assessment);
      return copy(assessment);
    },
    listAssessments(p) {
      const limit = p?.limit ?? 50;
      const offset = p?.offset ?? 0;
      const rows = store.assessments
        .filter((a) => (!p?.vendorId || a.vendor_id === p.vendorId) && (!p?.status || a.status === p.status))
        .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : a.id.localeCompare(b.id)));
      const page: Page<Assessment> = { items: copy(rows.slice(offset, offset + limit)), limit, offset };
      return page;
    },
    getAssessment(id) {
      return loadDetail(findAssessment(id));
    },
    setResponse(assessmentId, questionId, body) {
      const assessment = findAssessment(assessmentId);
      if (assessment.status === "scored") {
        throw new ApiError(409, "The assessment is final and can no longer change");
      }
      const version = findVersion(assessment.version_id);
      const question = activeQuestions(version).find((q) => q.id === questionId);
      const option = question?.options.find((o) => o.id === body.option_id && o.status === "active");
      if (!question || !option) {
        throw new ApiError(422, "Invalid question or option for this assessment");
      }
      if ((body.comment ?? "").length > 2000) throw invalid("comment", "String should have at most 2000 characters");
      let response = store.responses.find((r) => r.assessment_id === assessmentId && r.question_id === questionId);
      if (!response) {
        response = {
          id: uid(),
          assessment_id: assessmentId,
          question_id: questionId,
          option_id: option.id,
          comment: null,
          answered_by: USER_ID,
          updated_at: now(),
        };
        store.responses.push(response);
      }
      response.option_id = option.id;
      response.comment = body.comment?.trim() || null;
      response.updated_at = now();
      return copy(response);
    },
    scoreAssessment(id) {
      return score(findAssessment(id));
    },
    getResult(id) {
      return resultOf(findAssessment(id));
    },
  };

  const client = Object.fromEntries(
    Object.entries(ops).map(([name, fn]) => [
      name,
      async (...args: unknown[]) => {
        await wait();
        return (fn as (...a: unknown[]) => unknown)(...args);
      },
    ]),
  ) as unknown as ApiClient;

  if (options.seed !== false) seed(store, ops);
  return client;
}
/** Sample data for screenshots: clearly fictional vendors, scored through the real rules above. */
function seed(store: Store, ops: SyncApi) {
  const day = 86_400_000;
  const ago = (days: number) => new Date(Date.now() - days * day).toISOString();

  const starter = ops.createFromStarter();
  const versionId = starter.versions[0]?.id ?? "";
  ops.publishVersion(versionId);
  ops.createTemplate({ name: "Cloud provider review (draft)", description: "Work in progress" });

  type Pattern = "best" | "mixed" | "poor" | "worst" | "open";
  const plan: [string, number | null, Pattern][] = [
    ["Northwind Payments", 9, "best"],
    ["Contoso Cloud Hosting", 21, "mixed"],
    ["Fabrikam Analytics", 14, "poor"],
    ["Tailspin HR Suite", 6, "worst"],
    ["Adatum Backup Services", 30, "mixed"],
    ["Litware Messaging", 3, "open"],
    ["Proseware Identity", null, "open"],
    ["Wingtip Legal Portal", null, "open"],
  ];
  const vendors = plan.map(([name]) => ops.createVendor({ name }));
  const former = ops.createVendor({ name: "Woodgrove Print (former vendor)" });
  ops.updateVendor(former.id, { status: "archived" });

  plan.forEach(([, daysAgo, pattern], index) => {
    const vendor = vendors[index];
    if (!vendor || daysAgo === null) return;
    const assessment = ops.createAssessment(vendor.id, { version_id: versionId });
    const detail = ops.getAssessment(assessment.id);
    const limit = pattern === "open" ? Math.floor(detail.questions.length / 2) : detail.questions.length;
    detail.questions.forEach((question, qi) => {
      if (qi >= limit) return;
      const sorted = question.options
        .filter((o) => o.risk_points !== null)
        .sort((a, b) => (a.risk_points ?? 0) - (b.risk_points ?? 0));
      const pick =
        pattern === "best"
          ? sorted[0]
          : pattern === "worst"
            ? sorted[sorted.length - 1]
            : pattern === "poor"
              ? sorted[qi % 3 === 0 ? 0 : sorted.length - 1]
              : sorted[qi % sorted.length];
      if (pick) ops.setResponse(assessment.id, question.id, { option_id: pick.id });
    });
    if (pattern !== "open") ops.scoreAssessment(assessment.id);
    const row = store.assessments.find((a) => a.id === assessment.id);
    if (row) {
      row.created_at = ago(daysAgo + 1);
      row.updated_at = ago(daysAgo);
      if (row.scored_at) row.scored_at = ago(daysAgo);
    }
  });
  vendors.forEach((vendor, index) => {
    const row = store.vendors.find((v) => v.id === vendor.id);
    if (row) {
      row.created_at = ago(40 - index * 3);
      row.updated_at = row.created_at;
    }
  });
}
