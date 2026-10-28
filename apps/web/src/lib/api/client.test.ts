import { type ApiClient, createHttpClient, fetchAll } from "./client";
import { ApiError, fromResponseBody } from "./errors";

interface Call {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

function setup(
  respond: (call: Call) => { status?: number; body?: unknown } = () => ({ body: {} }),
  options: { token?: string | null; orgId?: string | null; onUnauthorized?: () => void } = {},
) {
  const calls: Call[] = [];
  const fetchImpl = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const call: Call = {
      url: String(url),
      method: init?.method ?? "GET",
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);
    const { status = 200, body = {} } = respond(call);
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  });
  const client = createHttpClient({
    baseUrl: "https://api.example.com/",
    getToken: async () => (options.token === undefined ? "token-123" : options.token),
    getOrgId: () => options.orgId ?? null,
    onUnauthorized: options.onUnauthorized,
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  return { client, calls };
}

const ID = "11111111-1111-4111-8111-111111111111";
const ID2 = "22222222-2222-4222-8222-222222222222";

describe("request contract (matches docs/api.md)", () => {
  it("sends the bearer token and never anything else for identity", async () => {
    const { client, calls } = setup();
    await client.listVendors();
    expect(calls[0]?.headers.Authorization).toBe("Bearer token-123");
    expect(Object.keys(calls[0]?.headers ?? {}).sort()).toEqual(["Accept", "Authorization"]);
  });

  it("adds X-Org-Id only when an organization was chosen", async () => {
    const { client, calls } = setup(undefined, { orgId: ID });
    await client.listVendors();
    expect(calls[0]?.headers["X-Org-Id"]).toBe(ID);
  });

  const CASES: [string, (c: ApiClient) => Promise<unknown>, string, string][] = [
    ["getMe", (c) => c.getMe(), "GET", "/me"],
    ["listVendors", (c) => c.listVendors({ limit: 100, offset: 200, status: "active" }), "GET", "/vendors?limit=100&offset=200&status=active"],
    ["getVendor", (c) => c.getVendor(ID), "GET", `/vendors/${ID}`],
    ["createVendor", (c) => c.createVendor({ name: "Acme" }), "POST", "/vendors"],
    ["updateVendor", (c) => c.updateVendor(ID, { status: "archived" }), "PATCH", `/vendors/${ID}`],
    ["listTemplates", (c) => c.listTemplates({ limit: 100, includeArchived: true }), "GET", "/questionnaires?limit=100&include_archived=true"],
    ["getTemplate", (c) => c.getTemplate(ID), "GET", `/questionnaires/${ID}`],
    ["createTemplate", (c) => c.createTemplate({ name: "Q" }), "POST", "/questionnaires"],
    ["createFromStarter", (c) => c.createFromStarter(), "POST", "/questionnaires/from-starter"],
    ["updateTemplate", (c) => c.updateTemplate(ID, { status: "archived" }), "PATCH", `/questionnaires/${ID}`],
    ["createVersion", (c) => c.createVersion(ID), "POST", `/questionnaires/${ID}/versions`],
    ["getVersion", (c) => c.getVersion(ID, true), "GET", `/questionnaire-versions/${ID}?include_archived=true`],
    ["updateThresholds", (c) => c.updateThresholds(ID, { low_max: 10, medium_max: 40, high_max: 90 }), "PATCH", `/questionnaire-versions/${ID}`],
    ["publishVersion", (c) => c.publishVersion(ID), "POST", `/questionnaire-versions/${ID}/publish`],
    ["retireVersion", (c) => c.retireVersion(ID), "POST", `/questionnaire-versions/${ID}/retire`],
    ["reorderQuestions", (c) => c.reorderQuestions(ID, [ID2]), "PUT", `/questionnaire-versions/${ID}/order`],
    ["addQuestion", (c) => c.addQuestion(ID, { category: "mfa", prompt: "Is MFA enforced?", weight: 3, options: [] }), "POST", `/questionnaire-versions/${ID}/questions`],
    ["updateQuestion", (c) => c.updateQuestion(ID, { weight: 4 }), "PATCH", `/questions/${ID}`],
    ["addOption", (c) => c.addOption(ID, { label: "Yes", risk_points: 0 }), "POST", `/questions/${ID}/options`],
    ["updateOption", (c) => c.updateOption(ID, { risk_points: null }), "PATCH", `/options/${ID}`],
    ["createAssessment", (c) => c.createAssessment(ID, { questionnaire_id: ID2 }), "POST", `/vendors/${ID}/assessments`],
    ["listAssessments", (c) => c.listAssessments({ vendorId: ID, status: "scored", limit: 50 }), "GET", `/assessments?vendor_id=${ID}&status=scored&limit=50`],
    ["getAssessment", (c) => c.getAssessment(ID), "GET", `/assessments/${ID}`],
    ["setResponse", (c) => c.setResponse(ID, ID2, { option_id: ID }), "PUT", `/assessments/${ID}/responses/${ID2}`],
    ["scoreAssessment", (c) => c.scoreAssessment(ID), "POST", `/assessments/${ID}/score`],
    ["getResult", (c) => c.getResult(ID), "GET", `/assessments/${ID}/result`],
  ];

  it.each(CASES)("%s uses %s %s", async (_name, call, method, path) => {
    const { client, calls } = setup();
    await call(client);
    expect(calls[0]?.method).toBe(method);
    expect(calls[0]?.url).toBe(`https://api.example.com${path}`);
  });

  it("sends JSON bodies exactly as the API schemas expect", async () => {
    const { client, calls } = setup();
    await client.addQuestion(ID, {
      category: "mfa",
      prompt: "Is MFA enforced?",
      weight: 5,
      options: [{ label: "Not applicable", risk_points: null }],
    });
    await client.setResponse(ID, ID2, { option_id: ID, comment: "see policy" });
    await client.reorderQuestions(ID, [ID2, ID]);
    expect(calls[0]?.headers["Content-Type"]).toBe("application/json");
    expect(calls[0]?.body).toEqual({
      category: "mfa",
      prompt: "Is MFA enforced?",
      weight: 5,
      options: [{ label: "Not applicable", risk_points: null }],
    });
    expect(calls[1]?.body).toEqual({ option_id: ID, comment: "see policy" });
    expect(calls[2]?.body).toEqual({ question_ids: [ID2, ID] });
  });

  it("omits undefined query parameters", async () => {
    const { client, calls } = setup();
    await client.listAssessments();
    await client.listVendors({ limit: 5 });
    expect(calls[0]?.url).toBe("https://api.example.com/assessments");
    expect(calls[1]?.url).toBe("https://api.example.com/vendors?limit=5");
  });
});

describe("failures", () => {
  it("does not call the API without a token", async () => {
    const { client, calls } = setup(undefined, { token: null });
    await expect(client.listVendors()).rejects.toMatchObject({ status: 401, kind: "unauthenticated" });
    expect(calls).toHaveLength(0);
  });

  it("signals an expired session on 401", async () => {
    const onUnauthorized = vi.fn();
    const { client } = setup(() => ({ status: 401, body: { detail: "Not authenticated" } }), { onUnauthorized });
    await expect(client.listVendors()).rejects.toMatchObject({ kind: "unauthenticated" });
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it.each([
    [400, "Choose an organization with the X-Org-Id header", "org_required"],
    [403, "No active membership", "no_membership"],
    [403, "Insufficient permissions", "forbidden"],
    [403, "Multi-factor authentication required", "mfa_required"],
    [404, "Vendor not found", "not_found"],
    [409, "The assessment is final and can no longer change", "conflict"],
    [503, "Authentication is not available", "unavailable"],
    [500, "Internal server error", "server"],
  ])("maps %i %s to %s", async (status, detail, kind) => {
    const { client } = setup(() => ({ status, body: { detail } }));
    await expect(client.getVendor(ID)).rejects.toMatchObject({ status, kind, message: detail });
  });

  it("turns a network failure into a clear error", async () => {
    const client = createHttpClient({
      baseUrl: "https://api.example.com",
      getToken: async () => "t",
      fetchImpl: (async () => {
        throw new TypeError("Failed to fetch");
      }) as unknown as typeof fetch,
    });
    await expect(client.listVendors()).rejects.toMatchObject({ status: 0, kind: "network" });
  });

  it("copes with a non-JSON error body", async () => {
    const client = createHttpClient({
      baseUrl: "https://api.example.com",
      getToken: async () => "t",
      fetchImpl: (async () => new Response("<html>bad gateway</html>", { status: 502 })) as unknown as typeof fetch,
    });
    const error = await client.listVendors().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe("Request failed (502).");
  });

  it("exposes the unanswered questions of an incomplete assessment", async () => {
    const { client } = setup(() => ({
      status: 409,
      body: { detail: "Every question must be answered", unanswered_question_ids: [ID, ID2] },
    }));
    const error = (await client.scoreAssessment(ID).catch((e: unknown) => e)) as ApiError;
    expect(error.unanswered).toEqual([ID, ID2]);
  });
});

describe("FastAPI validation errors", () => {
  it("maps 422 details to field errors", () => {
    const error = fromResponseBody(422, {
      detail: [
        { loc: ["body", "name"], msg: "String should have at least 1 character", type: "x" },
        { loc: ["body", "options", 0, "risk_points"], msg: "Value error, too big", type: "y" },
      ],
    });
    expect(error.kind).toBe("validation");
    expect(error.fieldErrors).toEqual({
      name: "String should have at least 1 character",
      "options.0.risk_points": "too big",
    });
  });
});

describe("fetchAll", () => {
  it("reads every page and stops at a short page", async () => {
    const pages: number[] = [];
    const items = await fetchAll(async ({ limit, offset }) => {
      pages.push(offset);
      const remaining = Math.max(0, 230 - offset);
      return { items: Array.from({ length: Math.min(limit, remaining) }, (_, i) => offset + i), limit, offset };
    });
    expect(items).toHaveLength(230);
    expect(pages).toEqual([0, 100, 200]);
  });

  it("is bounded", async () => {
    const items = await fetchAll(async ({ limit, offset }) => ({ items: Array(limit).fill(0), limit, offset }), 300);
    expect(items).toHaveLength(300);
  });
});
