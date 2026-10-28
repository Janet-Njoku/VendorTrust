/**
 * Typed client for the VendorTrust API (docs/api.md). Every endpoint used by the UI is a method
 * here, and nothing else in the app calls fetch for API data. Authorization is the caller's
 * Supabase access token; the organization, role and vendor scope are resolved by the backend from
 * the database, never sent by this client (X-Org-Id only selects among the user's own memberships).
 */

import { ApiError, fromResponseBody } from "./errors";
import type {
  Assessment,
  AssessmentCreate,
  AssessmentDetail,
  AssessmentResponse,
  AssessmentResult,
  AssessmentStatus,
  ListParams,
  Me,
  Option,
  OptionInput,
  OptionUpdate,
  Page,
  Question,
  QuestionInput,
  QuestionUpdate,
  ResponseInput,
  Template,
  TemplateCreate,
  TemplateDetail,
  TemplateUpdate,
  Thresholds,
  Uuid,
  Vendor,
  VendorCreate,
  VendorStatus,
  VendorUpdate,
  Version,
} from "./types";

export interface ApiClient {
  /** The signed-in user. The first call after sign-in also provisions their user row. */
  getMe(): Promise<Me>;
  // vendors
  listVendors(params?: ListParams & { status?: VendorStatus }): Promise<Page<Vendor>>;
  getVendor(id: Uuid): Promise<Vendor>;
  createVendor(body: VendorCreate): Promise<Vendor>;
  updateVendor(id: Uuid, body: VendorUpdate): Promise<Vendor>;
  // questionnaire templates
  listTemplates(params?: ListParams & { includeArchived?: boolean }): Promise<Template[]>;
  getTemplate(id: Uuid): Promise<TemplateDetail>;
  createTemplate(body: TemplateCreate): Promise<TemplateDetail>;
  createFromStarter(body?: { name?: string }): Promise<TemplateDetail>;
  updateTemplate(id: Uuid, body: TemplateUpdate): Promise<TemplateDetail>;
  createVersion(templateId: Uuid): Promise<Version>;
  // versions, questions, options
  getVersion(id: Uuid, includeArchived?: boolean): Promise<Version>;
  updateThresholds(id: Uuid, body: Thresholds): Promise<Version>;
  publishVersion(id: Uuid): Promise<Version>;
  retireVersion(id: Uuid): Promise<Version>;
  reorderQuestions(versionId: Uuid, questionIds: Uuid[]): Promise<Version>;
  addQuestion(versionId: Uuid, body: QuestionInput): Promise<Question>;
  updateQuestion(id: Uuid, body: QuestionUpdate): Promise<Question>;
  addOption(questionId: Uuid, body: OptionInput): Promise<Option>;
  updateOption(id: Uuid, body: OptionUpdate): Promise<Option>;
  // assessments
  createAssessment(vendorId: Uuid, body: AssessmentCreate): Promise<Assessment>;
  listAssessments(
    params?: ListParams & { vendorId?: Uuid; status?: AssessmentStatus },
  ): Promise<Page<Assessment>>;
  getAssessment(id: Uuid): Promise<AssessmentDetail>;
  setResponse(
    assessmentId: Uuid,
    questionId: Uuid,
    body: ResponseInput,
  ): Promise<AssessmentResponse>;
  scoreAssessment(id: Uuid): Promise<AssessmentResult>;
  getResult(id: Uuid): Promise<AssessmentResult>;
}

export interface HttpClientOptions {
  baseUrl: string;
  /** Returns a current access token, or null when nobody is signed in. */
  getToken: () => Promise<string | null>;
  /** The organization the user chose, for users who belong to several. */
  getOrgId?: () => string | null;
  /** Called when the API rejects the token (401), so the app can sign the user out. */
  onUnauthorized?: () => void;
  fetchImpl?: typeof fetch;
}

type Query = Record<string, string | number | boolean | undefined>;

function queryString(query: Query | undefined): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined) params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function createHttpClient(options: HttpClientOptions): ApiClient {
  const base = options.baseUrl.replace(/\/+$/, "");

  async function request<T>(
    method: string,
    path: string,
    init: { query?: Query; body?: unknown } = {},
  ): Promise<T> {
    const token = await options.getToken();
    if (!token) throw new ApiError(401, "Not signed in");
    const headers: Record<string, string> = {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
    };
    const orgId = options.getOrgId?.();
    if (orgId) headers["X-Org-Id"] = orgId;
    let body: string | undefined;
    if (init.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(init.body);
    }
    let response: Response;
    try {
      response = await (options.fetchImpl ?? fetch)(`${base}${path}${queryString(init.query)}`, {
        method,
        headers,
        body,
        cache: "no-store",
      });
    } catch {
      throw new ApiError(0, "The API could not be reached.");
    }
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const error = fromResponseBody(response.status, payload);
      if (error.kind === "unauthenticated") options.onUnauthorized?.();
      throw error;
    }
    return payload as T;
  }

  const get = <T>(path: string, query?: Query) => request<T>("GET", path, { query });
  const send = <T>(method: string, path: string, body?: unknown) =>
    request<T>(method, path, { body });

  return {
    getMe: () => get("/me"),
    listVendors: (p) => get("/vendors", { limit: p?.limit, offset: p?.offset, status: p?.status }),
    getVendor: (id) => get(`/vendors/${id}`),
    createVendor: (body) => send("POST", "/vendors", body),
    updateVendor: (id, body) => send("PATCH", `/vendors/${id}`, body),

    listTemplates: (p) =>
      get("/questionnaires", {
        limit: p?.limit,
        offset: p?.offset,
        include_archived: p?.includeArchived,
      }),
    getTemplate: (id) => get(`/questionnaires/${id}`),
    createTemplate: (body) => send("POST", "/questionnaires", body),
    createFromStarter: (body) => send("POST", "/questionnaires/from-starter", body ?? {}),
    updateTemplate: (id, body) => send("PATCH", `/questionnaires/${id}`, body),
    createVersion: (templateId) => send("POST", `/questionnaires/${templateId}/versions`),

    getVersion: (id, includeArchived) =>
      get(`/questionnaire-versions/${id}`, { include_archived: includeArchived }),
    updateThresholds: (id, body) => send("PATCH", `/questionnaire-versions/${id}`, body),
    publishVersion: (id) => send("POST", `/questionnaire-versions/${id}/publish`),
    retireVersion: (id) => send("POST", `/questionnaire-versions/${id}/retire`),
    reorderQuestions: (versionId, questionIds) =>
      send("PUT", `/questionnaire-versions/${versionId}/order`, { question_ids: questionIds }),
    addQuestion: (versionId, body) =>
      send("POST", `/questionnaire-versions/${versionId}/questions`, body),
    updateQuestion: (id, body) => send("PATCH", `/questions/${id}`, body),
    addOption: (questionId, body) => send("POST", `/questions/${questionId}/options`, body),
    updateOption: (id, body) => send("PATCH", `/options/${id}`, body),

    createAssessment: (vendorId, body) => send("POST", `/vendors/${vendorId}/assessments`, body),
    listAssessments: (p) =>
      get("/assessments", {
        vendor_id: p?.vendorId,
        status: p?.status,
        limit: p?.limit,
        offset: p?.offset,
      }),
    getAssessment: (id) => get(`/assessments/${id}`),
    setResponse: (assessmentId, questionId, body) =>
      send("PUT", `/assessments/${assessmentId}/responses/${questionId}`, body),
    scoreAssessment: (id) => send("POST", `/assessments/${id}/score`),
    getResult: (id) => get(`/assessments/${id}/result`),
  };
}

/** Read every page of a list endpoint (the API caps a page at 100 rows). */
export async function fetchAll<T>(
  page: (params: { limit: number; offset: number }) => Promise<Page<T>>,
  maxItems = 2000,
): Promise<T[]> {
  const limit = 100;
  const all: T[] = [];
  for (let offset = 0; offset < maxItems; offset += limit) {
    const result = await page({ limit, offset });
    all.push(...result.items);
    if (result.items.length < limit) break;
  }
  return all;
}
