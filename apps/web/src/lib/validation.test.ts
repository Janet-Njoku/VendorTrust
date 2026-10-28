import { validateVendorName } from "@/components/vendor-form";

import {
  emptyOption,
  emptyQuestionForm,
  slugify,
  toQuestionInput,
  validateOption,
  validateQuestionForm,
  validateThresholds,
} from "./validation";

describe("validateVendorName", () => {
  it("applies the API's rules", () => {
    expect(validateVendorName("Acme Hosting")).toBeNull();
    expect(validateVendorName("   ")).toMatch(/Enter/);
    expect(validateVendorName("x".repeat(201))).toMatch(/200/);
    expect(validateVendorName("bad\u0000name")).toMatch(/control/);
    expect(validateVendorName("x".repeat(200))).toBeNull();
  });
});

describe("question form", () => {
  const valid = () => ({
    ...emptyQuestionForm("mfa"),
    prompt: "Is multi-factor authentication enforced?",
  });

  it("accepts a valid form and converts it to the API shape", () => {
    expect(validateQuestionForm(valid())).toEqual({});
    expect(toQuestionInput({ ...valid(), guidance: "  ", weight: "7" })).toEqual({
      category: "mfa",
      prompt: "Is multi-factor authentication enforced?",
      guidance: null,
      weight: 7,
      options: [
        { label: "Yes", risk_points: 0 },
        { label: "No", risk_points: 100 },
      ],
    });
  });

  it.each([
    [{ category: "Access Control" }, "category"],
    [{ category: "x" }, "category"],
    [{ prompt: "Hi" }, "prompt"],
    [{ prompt: "x".repeat(1001) }, "prompt"],
    [{ weight: "0" }, "weight"],
    [{ weight: "11" }, "weight"],
    [{ weight: "2.5" }, "weight"],
    [{ weight: "" }, "weight"],
    [{ guidance: "x".repeat(2001) }, "guidance"],
    [{ options: [{ label: "Only", points: "0", notApplicable: false }] }, "options"],
    [{ options: [{ label: "", points: "0", notApplicable: false }, emptyOption()] }, "options"],
    [{ options: [{ label: "A", points: "101", notApplicable: false }, { label: "B", points: "0", notApplicable: false }] }, "options"],
    [{ options: [{ label: "A", points: "abc", notApplicable: false }, { label: "B", points: "0", notApplicable: false }] }, "options"],
  ])("rejects %j", (override, field) => {
    expect(Object.keys(validateQuestionForm({ ...valid(), ...override }))).toContain(field);
  });

  it("treats not-applicable options as needing no points, and sends null", () => {
    const form = {
      ...valid(),
      options: [
        { label: "Yes", points: "0", notApplicable: false },
        { label: "Not applicable", points: "", notApplicable: true },
      ],
    };
    expect(validateQuestionForm(form)).toEqual({});
    expect(toQuestionInput(form).options[1]).toEqual({ label: "Not applicable", risk_points: null });
  });

  it("can skip the option checks when editing only the question's fields", () => {
    expect(validateQuestionForm({ ...valid(), options: [] }, false)).toEqual({});
  });

  it("validates single options", () => {
    expect(validateOption({ label: "Yes", points: "0", notApplicable: false })).toBeNull();
    expect(validateOption({ label: "Yes", points: "", notApplicable: false })).toMatch(/whole number/);
    expect(validateOption({ label: "x".repeat(201), points: "0", notApplicable: false })).toMatch(/200/);
  });
});

describe("slugify and thresholds", () => {
  it("makes a valid category slug", () => {
    expect(slugify("  Access Control & MFA! ")).toBe("access_control_mfa");
    expect(slugify("---")).toBe("");
  });

  it("requires strictly increasing thresholds up to 100", () => {
    expect(validateThresholds(25, 50, 75)).toBeNull();
    expect(validateThresholds(10, 40, 100)).toBeNull();
    const invalid: [number, number, number][] = [[0, 50, 75], [50, 50, 75], [60, 50, 75], [25, 50, 101], [2.5, 50, 75]];
    for (const [low, medium, high] of invalid) {
      expect(validateThresholds(low, medium, high)).not.toBeNull();
    }
  });
});
