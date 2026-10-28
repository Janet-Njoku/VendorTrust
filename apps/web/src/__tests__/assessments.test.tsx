import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import AssessmentPage from "@/app/(app)/assessments/[id]/page";
import AssessmentsPage from "@/app/(app)/assessments/page";
import { ApiError } from "@/lib/api/errors";
import { createDemoClient } from "@/lib/demo/client";
import { renderApp } from "@/test/helpers";
import { nav, resetNav } from "@/test/router";

beforeEach(() => resetNav());

const OPTIONS = [
  { label: "Yes", risk_points: 0 },
  { label: "Partial", risk_points: 50 },
  { label: "No", risk_points: 100 },
  { label: "N/A", risk_points: null },
];

/** A small published questionnaire (weights 5 and 3) and an open assessment of one vendor. */
async function openAssessment() {
  const client = createDemoClient({ seed: false });
  const template = await client.createTemplate({ name: "Baseline" });
  const versionId = template.versions[0]?.id ?? "";
  await client.addQuestion(versionId, { category: "mfa", prompt: "Is MFA required for admins?", weight: 5, options: OPTIONS });
  await client.addQuestion(versionId, { category: "encryption", prompt: "Is data encrypted at rest?", weight: 3, options: OPTIONS });
  await client.publishVersion(versionId);
  const vendor = await client.createVendor({ name: "Acme Hosting" });
  const assessment = await client.createAssessment(vendor.id, { questionnaire_id: template.id });
  return { client, template, vendor, assessment };
}

const group = (prompt: RegExp) => screen.getByRole("group", { name: prompt });
const pick = (prompt: RegExp, label: string) => fireEvent.click(within(group(prompt)).getByLabelText(label));

describe("Assessment list", () => {
  it("lists assessments with their vendor and result, and filters them", async () => {
    renderApp(<AssessmentsPage />);
    const table = await screen.findByRole("table", { name: "Assessments" });
    expect(within(table).getAllByRole("row")).toHaveLength(7); // header + 6
    const row = within(table).getByText("Tailspin HR Suite").closest("tr") as HTMLElement;
    expect(within(row).getByText(/Critical/)).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: /View result/ })).toHaveAttribute("href", expect.stringMatching(/^\/assessments\//));

    fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "in_progress" } });
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getByText("Litware Messaging")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: /Continue/ })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "all" } });
    fireEvent.change(screen.getByLabelText("Filter by vendor"), { target: { value: "" } });
    expect(within(table).getAllByRole("row")).toHaveLength(7);
  });

  it("starts an assessment for a chosen vendor and questionnaire", async () => {
    const client = createDemoClient();
    const create = vi.spyOn(client, "createAssessment");
    renderApp(<AssessmentsPage />, { client });
    await screen.findByRole("table", { name: "Assessments" });
    fireEvent.click(screen.getByRole("button", { name: "New assessment" }));
    const dialog = screen.getByRole("dialog", { name: "New assessment" });
    const submit = await within(dialog).findByRole("button", { name: "Start assessment" });

    fireEvent.click(submit);
    expect(await within(dialog).findByText("Choose a vendor.")).toBeInTheDocument();

    const vendor = within(dialog).getByLabelText("Vendor");
    expect(within(vendor).queryByText(/Woodgrove/)).not.toBeInTheDocument(); // archived vendors cannot be assessed
    fireEvent.change(vendor, { target: { value: (within(vendor).getByRole("option", { name: "Proseware Identity" }) as HTMLOptionElement).value } });
    fireEvent.click(submit);
    expect(await within(dialog).findByText("Choose a questionnaire.")).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();

    const questionnaire = within(dialog).getByLabelText("Questionnaire");
    fireEvent.change(questionnaire, { target: { value: (within(questionnaire).getByRole("option", { name: /starter/ }) as HTMLOptionElement).value } });
    fireEvent.click(submit);
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith(expect.stringMatching(/^\/assessments\/.+/)));
    expect(create).toHaveBeenCalledWith(expect.any(String), { questionnaire_id: expect.any(String) });
  });

  it("shows the API's reason when a questionnaire has no published version", async () => {
    const client = createDemoClient();
    const draft = (await client.listTemplates()).find((t) => t.name.startsWith("Cloud provider"));
    renderApp(<AssessmentsPage />, { client });
    await screen.findByRole("table", { name: "Assessments" });
    fireEvent.click(screen.getByRole("button", { name: "New assessment" }));
    const dialog = screen.getByRole("dialog");
    await within(dialog).findByLabelText("Questionnaire");
    const vendor = within(dialog).getByLabelText("Vendor");
    fireEvent.change(vendor, { target: { value: (within(vendor).getByRole("option", { name: "Proseware Identity" }) as HTMLOptionElement).value } });
    fireEvent.change(within(dialog).getByLabelText("Questionnaire"), { target: { value: draft?.id } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Start assessment" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/no published version/);
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("explains an empty list", async () => {
    renderApp(<AssessmentsPage />, { client: createDemoClient({ seed: false }) });
    expect(await screen.findByText("No assessments yet")).toBeInTheDocument();
  });
});

describe("Answering and scoring", () => {
  it("saves each answer, enables finalizing only when complete, and shows the weighted result", async () => {
    const { client, assessment } = await openAssessment();
    const save = vi.spyOn(client, "setResponse");
    resetNav({ id: assessment.id });
    renderApp(<AssessmentPage />, { client });

    expect(await screen.findByRole("heading", { level: 1, name: "Assessment: Acme Hosting" })).toBeInTheDocument();
    const finalize = screen.getByRole("button", { name: "Finalize and score" });
    expect(finalize).toBeDisabled();
    expect(screen.getByText("0 of 2 answered")).toBeInTheDocument();

    pick(/MFA required/, "No");
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 answered")).toBeInTheDocument();
    expect(finalize).toBeDisabled();
    expect(save).toHaveBeenCalledTimes(1);

    pick(/encrypted at rest/, "Yes");
    await waitFor(() => expect(screen.getByText("2 of 2 answered")).toBeInTheDocument());
    await waitFor(() => expect(finalize).toBeEnabled());
    expect(screen.getByRole("progressbar", { name: "Questions answered" })).toHaveAttribute("aria-valuenow", "2");

    fireEvent.click(finalize);
    // (5 x 100 + 3 x 0) / 8 = 62.5 -> High
    expect(await screen.findByRole("img", { name: /Risk score 62\.5 out of 100, High risk/ })).toBeInTheDocument();
    const breakdown = screen.getByRole("table", { name: /Contribution of each question/ });
    const rows = within(breakdown).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent(/MFA required for admins/);
    expect(rows[1]).toHaveTextContent("62.5"); // 5 x 100 / 8
    expect(rows[2]).toHaveTextContent("0.0");
    expect(screen.getByText("Score by category")).toBeInTheDocument();
    expect(screen.getByText(/Thresholds frozen with this questionnaire version/)).toHaveTextContent(/High applies at 62\.5/);
    expect(screen.queryByRole("button", { name: "Finalize and score" })).not.toBeInTheDocument();
  });

  it("excludes not-applicable answers from the score and marks them in the breakdown", async () => {
    const { client, assessment } = await openAssessment();
    resetNav({ id: assessment.id });
    renderApp(<AssessmentPage />, { client });
    await screen.findByRole("heading", { level: 1, name: "Assessment: Acme Hosting" });
    pick(/MFA required/, "N/A");
    pick(/encrypted at rest/, "Partial");
    await waitFor(() => expect(screen.getByRole("button", { name: "Finalize and score" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Finalize and score" }));
    expect(await screen.findByRole("img", { name: /Risk score 50\.0 out of 100, High risk/ })).toBeInTheDocument();
    const rows = within(screen.getByRole("table", { name: /Contribution/ })).getAllByRole("row");
    expect(rows.some((r) => /excluded/.test(r.textContent ?? ""))).toBe(true);
  });

  it("keeps an optional note with the answer", async () => {
    const { client, assessment } = await openAssessment();
    const save = vi.spyOn(client, "setResponse");
    resetNav({ id: assessment.id });
    renderApp(<AssessmentPage />, { client });
    await screen.findByRole("heading", { level: 1, name: "Assessment: Acme Hosting" });
    pick(/MFA required/, "Partial");
    const note = screen.getByLabelText(/Note for: Is MFA required/);
    fireEvent.change(note, { target: { value: "Rolling out in Q3" } });
    fireEvent.blur(note);
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(save).toHaveBeenLastCalledWith(assessment.id, expect.any(String), { option_id: expect.any(String), comment: "Rolling out in Q3" });
    const detail = await client.getAssessment(assessment.id);
    expect(detail.questions[0]?.response?.comment).toBe("Rolling out in Q3");
  });

  it("restores saved answers when the assessment is reopened", async () => {
    const { client, assessment } = await openAssessment();
    const detail = await client.getAssessment(assessment.id);
    const q = detail.questions[0];
    await client.setResponse(assessment.id, q?.id ?? "", { option_id: q?.options[1]?.id ?? "" });
    resetNav({ id: assessment.id });
    renderApp(<AssessmentPage />, { client });
    await screen.findByRole("heading", { level: 1, name: "Assessment: Acme Hosting" });
    expect(within(group(/MFA required/)).getByLabelText("Partial")).toBeChecked();
    expect(screen.getByText("1 of 2 answered")).toBeInTheDocument();
  });

  it("shows a save failure on the question and lets the user try again", async () => {
    const { client, assessment } = await openAssessment();
    let fail = true;
    const real = client.setResponse;
    client.setResponse = (...args) => (fail ? Promise.reject(new ApiError(0, "down")) : real(...args));
    resetNav({ id: assessment.id });
    renderApp(<AssessmentPage />, { client });
    await screen.findByRole("heading", { level: 1, name: "Assessment: Acme Hosting" });
    pick(/MFA required/, "Yes");
    expect(await screen.findByText(/could not be reached/i)).toBeInTheDocument();
    fail = false;
    pick(/MFA required/, "No");
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("highlights questions the server says are unanswered", async () => {
    const { client, assessment } = await openAssessment();
    const detail = await client.getAssessment(assessment.id);
    for (const q of detail.questions) await client.setResponse(assessment.id, q.id, { option_id: q.options[0]?.id ?? "" });
    client.scoreAssessment = () =>
      Promise.reject(new ApiError(409, "Every question must be answered before the assessment can be scored", { unanswered: [] }));
    resetNav({ id: assessment.id });
    renderApp(<AssessmentPage />, { client });
    await screen.findByRole("heading", { level: 1, name: "Assessment: Acme Hosting" });
    fireEvent.click(screen.getByRole("button", { name: "Finalize and score" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Every question must be answered/);
  });

  it("opens a scored assessment straight to its result, read-only", async () => {
    const { client, assessment } = await openAssessment();
    const detail = await client.getAssessment(assessment.id);
    for (const q of detail.questions) await client.setResponse(assessment.id, q.id, { option_id: q.options[0]?.id ?? "" });
    await client.scoreAssessment(assessment.id);
    resetNav({ id: assessment.id });
    renderApp(<AssessmentPage />, { client });
    expect(await screen.findByRole("img", { name: /Risk score 0\.0 out of 100, Low risk/ })).toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });

  it("reports an assessment that cannot be found", async () => {
    resetNav({ id: "00000000-0000-4000-8000-00000000dead" });
    renderApp(<AssessmentPage />);
    expect(await screen.findByText("Not found")).toBeInTheDocument();
  });
});
