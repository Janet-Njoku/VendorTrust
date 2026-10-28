import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import QuestionnairesPage from "@/app/(app)/questionnaires/page";
import TemplatePage from "@/app/(app)/questionnaires/[id]/page";
import VersionPage from "@/app/(app)/questionnaires/[id]/versions/[versionId]/page";
import { ApiError } from "@/lib/api/errors";
import { createDemoClient } from "@/lib/demo/client";
import { renderApp } from "@/test/helpers";
import { nav, resetNav } from "@/test/router";

beforeEach(() => resetNav());

async function starterDraft() {
  const client = createDemoClient({ seed: false });
  const template = await client.createFromStarter();
  const versionId = template.versions[0]?.id ?? "";
  return { client, template, versionId };
}

function renderEditor(client: ReturnType<typeof createDemoClient>, templateId: string, versionId: string) {
  resetNav({ id: templateId, versionId });
  return renderApp(<VersionPage />, { client });
}

const card = (text: RegExp) => screen.getByText(text).closest("li") as HTMLElement;

describe("Questionnaire list", () => {
  it("offers the starter when there are none, and opens the new questionnaire", async () => {
    const client = createDemoClient({ seed: false });
    renderApp(<QuestionnairesPage />, { client });
    expect(await screen.findByText("No questionnaires yet")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Create from starter" })[1] as HTMLElement);
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith(expect.stringMatching(/^\/questionnaires\/[^/]+$/)));
    const [created] = await client.listTemplates();
    expect(created?.name).toMatch(/starter/);
  });

  it("lists templates and hides archived ones until asked", async () => {
    const client = createDemoClient();
    const archived = await client.createTemplate({ name: "Old review" });
    await client.updateTemplate(archived.id, { status: "archived" });
    renderApp(<QuestionnairesPage />, { client });
    expect(await screen.findByRole("link", { name: /starter/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Old review" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Show archived"));
    expect(await screen.findByRole("link", { name: "Old review" })).toBeInTheDocument();
  });

  it("validates and creates a questionnaire", async () => {
    const client = createDemoClient({ seed: false });
    const create = vi.spyOn(client, "createTemplate");
    await client.createTemplate({ name: "Existing" });
    renderApp(<QuestionnairesPage />, { client });
    await screen.findByRole("link", { name: "Existing" });
    fireEvent.click(screen.getByRole("button", { name: "New questionnaire" }));
    const dialog = screen.getByRole("dialog");
    create.mockClear();
    fireEvent.click(within(dialog).getByRole("button", { name: "Create questionnaire" }));
    expect(await within(dialog).findByText("Give the questionnaire a name.")).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Cloud review" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create questionnaire" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalled());
    expect(create).toHaveBeenCalledWith({ name: "Cloud review", description: null });
  });
});

describe("Template page", () => {
  it("shows the versions and continues the draft", async () => {
    const { client, template } = await starterDraft();
    resetNav({ id: template.id });
    renderApp(<TemplatePage />, { client });
    expect(await screen.findByRole("heading", { level: 1, name: /starter/ })).toBeInTheDocument();
    const row = screen.getByText("v1").closest("tr") as HTMLElement;
    expect(within(row).getByText("draft")).toBeInTheDocument();
    expect(within(row).getByText("15")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /continue editing draft/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New draft version" })).not.toBeInTheDocument();
  });

  it("starts a new draft from a published version and archives the questionnaire", async () => {
    const { client, template, versionId } = await starterDraft();
    await client.publishVersion(versionId);
    resetNav({ id: template.id });
    renderApp(<TemplatePage />, { client });
    fireEvent.click(await screen.findByRole("button", { name: "New draft version" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith(expect.stringMatching(new RegExp(`^/questionnaires/${template.id}/versions/.+`))));
    expect((await client.getTemplate(template.id)).versions).toHaveLength(2);
  });

  it("shows the API's message when a second draft is refused", async () => {
    const { client, template, versionId } = await starterDraft();
    await client.publishVersion(versionId);
    client.createVersion = () => Promise.reject(new ApiError(409, "A draft version already exists; edit or retire it first"));
    resetNav({ id: template.id });
    renderApp(<TemplatePage />, { client });
    fireEvent.click(await screen.findByRole("button", { name: "New draft version" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A draft version already exists");
  });
});

describe("Draft editor", () => {
  it("shows the questions with their weights and options", async () => {
    const { client, template, versionId } = await starterDraft();
    renderEditor(client, template.id, versionId);
    expect(await screen.findByRole("heading", { level: 1, name: "Version 1" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").length).toBeGreaterThan(15);
    const first = card(/least-privilege/);
    expect(within(first).getByText(/Access Control · weight 4/)).toBeInTheDocument();
    expect(within(first).getByText("Fully implemented")).toBeInTheDocument();
    expect(within(first).getAllByText("N/A")).toHaveLength(1);
    expect(screen.getByLabelText("Low below")).toHaveValue("25");
  });

  it("edits a question and rejects invalid values without calling the API", async () => {
    const { client, template, versionId } = await starterDraft();
    const update = vi.spyOn(client, "updateQuestion");
    renderEditor(client, template.id, versionId);
    await screen.findByRole("heading", { level: 1, name: "Version 1" });
    fireEvent.click(screen.getByRole("button", { name: "Edit question 1" }));
    const dialog = screen.getByRole("dialog", { name: "Edit question" });
    fireEvent.change(within(dialog).getByLabelText(/Weight/), { target: { value: "11" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save question" }));
    expect(await within(dialog).findByText(/whole number from 1 to 10/)).toBeInTheDocument();
    expect(update).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText(/Weight/), { target: { value: "9" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save question" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText(/Access Control · weight 9/)).toBeInTheDocument();
  });

  it("adds a question with options, including a not-applicable one", async () => {
    const { client, template, versionId } = await starterDraft();
    renderEditor(client, template.id, versionId);
    await screen.findByRole("heading", { level: 1, name: "Version 1" });
    fireEvent.click(screen.getByRole("button", { name: "Add question" }));
    const dialog = screen.getByRole("dialog", { name: "Add question" });

    fireEvent.click(within(dialog).getByRole("button", { name: "Add question" }));
    expect(await within(dialog).findByText(/at least 5 characters/)).toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText("Category"), { target: { value: "physical_security" } });
    fireEvent.change(within(dialog).getByLabelText("Question"), { target: { value: "Is the data centre access controlled?" } });
    fireEvent.change(within(dialog).getByLabelText("Weight (1 to 10)"), { target: { value: "6" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "+ Add option" }));
    fireEvent.change(within(dialog).getByLabelText("Option 3 label"), { target: { value: "Not applicable" } });
    fireEvent.click(within(dialog).getByLabelText("Option 3 not applicable"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Add question" }));

    expect(await screen.findByText(/Physical Security · weight 6/)).toBeInTheDocument();
    const version = await client.getVersion(versionId);
    const added = version.questions.find((q) => q.category === "physical_security");
    expect(added?.options.map((o) => o.risk_points)).toEqual([0, 100, null]);
  });

  it("validates answer options", async () => {
    const { client, template, versionId } = await starterDraft();
    renderEditor(client, template.id, versionId);
    await screen.findByRole("heading", { level: 1, name: "Version 1" });
    fireEvent.click(screen.getByRole("button", { name: "Add question" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Category"), { target: { value: "mfa" } });
    fireEvent.change(within(dialog).getByLabelText("Question"), { target: { value: "Is MFA enforced everywhere?" } });
    fireEvent.change(within(dialog).getByLabelText("Option 2 risk points"), { target: { value: "150" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add question" }));
    expect(await within(dialog).findByText(/between 0 and 100/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("archives and restores a question", async () => {
    const { client, template, versionId } = await starterDraft();
    renderEditor(client, template.id, versionId);
    await screen.findByRole("heading", { level: 1, name: "Version 1" });
    fireEvent.click(screen.getByRole("button", { name: "Archive question 1" }));
    await waitFor(() => expect(screen.queryByText(/Is access to systems and customer data/)).toBeInTheDocument());
    const archived = await screen.findByText("Archived questions (1)");
    const section = archived.closest("section") as HTMLElement;
    expect(within(section).getByText(/least-privilege/)).toBeInTheDocument();
    expect(screen.getByText(/· 14 questions/)).toBeInTheDocument();
    fireEvent.click(within(section).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(screen.getByText(/· 15 questions/)).toBeInTheDocument());
  });

  it("reorders questions", async () => {
    const { client, template, versionId } = await starterDraft();
    renderEditor(client, template.id, versionId);
    await screen.findByRole("heading", { level: 1, name: "Version 1" });
    expect(screen.getByRole("button", { name: "Move question 1 up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move question 15 down" })).toBeDisabled();
    const before = (await client.getVersion(versionId)).questions.map((q) => q.id);
    fireEvent.click(screen.getByRole("button", { name: "Move question 1 down" }));
    await waitFor(async () => {
      const after = (await client.getVersion(versionId)).questions.map((q) => q.id);
      expect(after.slice(0, 2)).toEqual([before[1], before[0]]);
    });
    await waitFor(() =>
      expect(screen.getAllByText(/^\d+\. /).slice(0, 2).map((e) => e.textContent)).toEqual([
        "1. Access Control · weight 3",
        "2. Access Control · weight 4",
      ]),
    );
  });

  it("changes the risk thresholds and refuses an invalid set", async () => {
    const { client, template, versionId } = await starterDraft();
    renderEditor(client, template.id, versionId);
    await screen.findByRole("heading", { level: 1, name: "Version 1" });
    fireEvent.change(screen.getByLabelText("Low below"), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: "Save thresholds" }));
    expect(await screen.findByText(/Thresholds must satisfy/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Low below"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Medium below"), { target: { value: "40" } });
    fireEvent.change(screen.getByLabelText("High below"), { target: { value: "90" } });
    fireEvent.click(screen.getByRole("button", { name: "Save thresholds" }));
    await waitFor(async () => expect(await client.getVersion(versionId)).toMatchObject({ low_max: 10, medium_max: 40, high_max: 90 }));
  });

  it("publishes after confirmation and then becomes read-only", async () => {
    const { client, template, versionId } = await starterDraft();
    renderEditor(client, template.id, versionId);
    await screen.findByRole("heading", { level: 1, name: "Version 1" });
    fireEvent.click(screen.getByRole("button", { name: "Publish version" }));
    const dialog = screen.getByRole("dialog", { name: "Publish this version?" });
    expect(dialog).toHaveTextContent(/cannot be undone/);
    fireEvent.click(within(dialog).getByRole("button", { name: "Publish" }));
    expect(await screen.findByText(/cannot be edited/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add question" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit question/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Low below")).toBeDisabled();
    expect(screen.getByRole("button", { name: /create new draft from this version/i })).toBeInTheDocument();
    expect((await client.getVersion(versionId)).status).toBe("published");
  });

  it("clones a published version for editing", async () => {
    const { client, template, versionId } = await starterDraft();
    await client.publishVersion(versionId);
    renderEditor(client, template.id, versionId);
    fireEvent.click(await screen.findByRole("button", { name: /create new draft from this version/i }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith(expect.stringMatching(new RegExp(`^/questionnaires/${template.id}/versions/.+`))));
    const detail = await client.getTemplate(template.id);
    expect(detail.versions.map((v) => [v.version_number, v.status])).toEqual([[2, "draft"], [1, "published"]]);
  });

  it("shows why a version cannot be published", async () => {
    const client = createDemoClient({ seed: false });
    const template = await client.createTemplate({ name: "Empty one" });
    const versionId = template.versions[0]?.id ?? "";
    renderEditor(client, template.id, versionId);
    expect(await screen.findByText("No questions yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Publish version" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Publish" }));
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent(/not ready to publish/);
  });

  it("discards a draft after confirmation", async () => {
    const { client, template, versionId } = await starterDraft();
    renderEditor(client, template.id, versionId);
    await screen.findByRole("heading", { level: 1, name: "Version 1" });
    fireEvent.click(screen.getByRole("button", { name: "Discard draft" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Discard draft" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith(`/questionnaires/${template.id}`));
    expect((await client.getVersion(versionId)).status).toBe("retired");
  });
});
