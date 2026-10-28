import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { ApiError } from "../api/errors";
import { classifyScore, createDemoClient, weightedScore } from "./client";
import { STARTER_QUESTIONS } from "./starter";

const YES_NO = [
  { label: "Yes", risk_points: 0 },
  { label: "No", risk_points: 100 },
];

async function published(client = createDemoClient({ seed: false })) {
  const template = await client.createTemplate({ name: "T" });
  const versionId = template.versions[0]?.id ?? "";
  const heavy = await client.addQuestion(versionId, { category: "mfa", prompt: "Is MFA required?", weight: 3, options: YES_NO });
  const light = await client.addQuestion(versionId, { category: "encryption", prompt: "Is data encrypted?", weight: 1, options: YES_NO });
  await client.publishVersion(versionId);
  const vendor = await client.createVendor({ name: "V" });
  return { client, template, versionId, heavy, light, vendor };
}

describe("demo scoring matches the backend rules", () => {
  it("weighted average, half-up rounding, N/A excluded", () => {
    expect(weightedScore([{ weight: 3, risk: 100 }, { weight: 1, risk: 0 }])).toBe(75);
    expect(weightedScore([{ weight: 5, risk: 0 }, { weight: 4, risk: 50 }, { weight: 3, risk: 100 }, { weight: 2, risk: 25 }])).toBe(39.3);
    expect(weightedScore([{ weight: 1, risk: 100 }, ...Array(15).fill({ weight: 1, risk: 0 })])).toBe(6.3);
    expect(weightedScore([{ weight: 5, risk: null }, { weight: 5, risk: 40 }])).toBe(40);
    expect(weightedScore([{ weight: 5, risk: null }])).toBeNull();
  });

  it("classifies with lower-inclusive bands", () => {
    const t = { low_max: 25, medium_max: 50, high_max: 75 };
    expect([0, 24.9, 25, 49.9, 50, 74.9, 75, 100].map((s) => classifyScore(s, t))).toEqual([
      "low", "low", "medium", "medium", "high", "high", "critical", "critical",
    ]);
  });
});

describe("demo starter parity with the backend", () => {
  it("has the same questions, in the same order, as apps/api/app/questionnaire_starter.py", () => {
    const source = readFileSync(resolve(__dirname, "../../../../api/app/questionnaire_starter.py"), "utf8");
    const prompts = [...source.matchAll(/^\s+"(.*\?)",$/gm)].map((m) => m[1]);
    expect(prompts).toEqual(STARTER_QUESTIONS.map((q) => q.prompt));
    const categories = [...source.matchAll(/^\s+"([a-z_]+)",$/gm)].map((m) => m[1]);
    expect(categories).toEqual(STARTER_QUESTIONS.map((q) => q.category));
  });
});

describe("demo client workflow rules", () => {
  it("freezes published versions", async () => {
    const { client, versionId, heavy } = await published();
    await expect(client.addQuestion(versionId, { category: "mfa", prompt: "Another question?", weight: 1, options: YES_NO })).rejects.toMatchObject({ status: 409 });
    await expect(client.updateQuestion(heavy.id, { weight: 9 })).rejects.toMatchObject({ status: 409 });
    await expect(client.publishVersion(versionId)).rejects.toMatchObject({ status: 409 });
  });

  it("refuses to publish a version that is not ready", async () => {
    const client = createDemoClient({ seed: false });
    const template = await client.createTemplate({ name: "Empty" });
    await expect(client.publishVersion(template.versions[0]?.id ?? "")).rejects.toThrow(/not ready to publish/);
  });

  it("clones into a new draft, once, and leaves the published version alone", async () => {
    const { client, template, versionId } = await published();
    const draft = await client.createVersion(template.id);
    expect(draft.version_number).toBe(2);
    expect(draft.questions).toHaveLength(2);
    await expect(client.createVersion(template.id)).rejects.toMatchObject({ status: 409 });
    await client.updateQuestion(draft.questions[0]?.id ?? "", { weight: 10 });
    const original = await client.getVersion(versionId);
    expect(original.questions.map((q) => q.weight)).toEqual([3, 1]);
  });

  it("scores an assessment and keeps it final", async () => {
    const { client, template, heavy, light, vendor } = await published();
    const a = await client.createAssessment(vendor.id, { questionnaire_id: template.id });
    await expect(client.scoreAssessment(a.id)).rejects.toMatchObject({ unanswered: [heavy.id, light.id] });
    await client.setResponse(a.id, heavy.id, { option_id: heavy.options.find((o) => o.label === "No")?.id ?? "" });
    await client.setResponse(a.id, light.id, { option_id: light.options.find((o) => o.label === "Yes")?.id ?? "" });
    const result = await client.scoreAssessment(a.id);
    expect(result.score).toBe(75);
    expect(result.risk_level).toBe("critical");
    expect(result.breakdown.categories.mfa?.score).toBe(100);
    await expect(client.setResponse(a.id, light.id, { option_id: light.options[1]?.id ?? "" })).rejects.toMatchObject({ status: 409 });
    await expect(client.scoreAssessment(a.id)).rejects.toMatchObject({ status: 409 });
    expect((await client.getResult(a.id)).score).toBe(75);
  });

  it("will not start an assessment on an unpublished or archived thing", async () => {
    const client = createDemoClient({ seed: false });
    const draft = await client.createTemplate({ name: "Draft only" });
    const vendor = await client.createVendor({ name: "V" });
    await expect(client.createAssessment(vendor.id, { questionnaire_id: draft.id })).rejects.toThrow(/no published version/);
    const { client: c2, template, vendor: v2 } = await published();
    await c2.updateVendor(v2.id, { status: "archived" });
    await expect(c2.createAssessment(v2.id, { questionnaire_id: template.id })).rejects.toMatchObject({ status: 409 });
  });

  it("rejects responses that do not belong to the assessment", async () => {
    const { client, template, heavy, light, vendor } = await published();
    const a = await client.createAssessment(vendor.id, { questionnaire_id: template.id });
    await expect(client.setResponse(a.id, heavy.id, { option_id: light.options[0]?.id ?? "" })).rejects.toMatchObject({ status: 422 });
  });

  it("validates input like the API", async () => {
    const client = createDemoClient({ seed: false });
    const error = (await client.createVendor({ name: "   " }).catch((e: unknown) => e)) as ApiError;
    expect(error.fieldErrors.name).toBeDefined();
    await expect(client.createVendor({ name: "x".repeat(201) })).rejects.toMatchObject({ status: 422 });
  });

  it("is isolated per client and never touches the network", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const one = createDemoClient({ seed: false });
    const two = createDemoClient({ seed: false });
    await one.createVendor({ name: "Only in one" });
    expect((await two.listVendors()).items).toHaveLength(0);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("seeds a believable portfolio with every risk level represented", async () => {
    const client = createDemoClient();
    const { items: vendors } = await client.listVendors({ limit: 100 });
    const { items: assessments } = await client.listAssessments({ limit: 100 });
    expect(vendors.length).toBeGreaterThanOrEqual(8);
    expect(vendors.some((v) => v.status === "archived")).toBe(true);
    expect(new Set(assessments.filter((a) => a.status === "scored").map((a) => a.risk_level)).size).toBeGreaterThanOrEqual(3);
    expect(assessments.some((a) => a.status === "in_progress")).toBe(true);
  });
});
