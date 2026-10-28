import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import VendorsPage from "@/app/(app)/vendors/page";
import VendorDetailPage from "@/app/(app)/vendors/[id]/page";
import { ApiError } from "@/lib/api/errors";
import { createDemoClient } from "@/lib/demo/client";
import { failingClient, renderApp } from "@/test/helpers";
import { nav, resetNav } from "@/test/router";

beforeEach(() => resetNav());

const rowFor = (name: string) => screen.getByRole("link", { name }).closest("tr") as HTMLElement;

describe("Vendor register", () => {
  it("lists vendors with their current risk", async () => {
    renderApp(<VendorsPage />);
    expect(screen.getByRole("status", { name: /loading vendors/i })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Northwind Payments" })).toBeInTheDocument();
    expect(within(rowFor("Tailspin HR Suite")).getByText(/Critical/)).toBeInTheDocument();
    expect(within(rowFor("Proseware Identity")).getByText("Not assessed")).toBeInTheDocument();
    expect(screen.getByText("Showing 9 of 9 vendors")).toBeInTheDocument();
  });

  it("searches by name and combines filters", async () => {
    renderApp(<VendorsPage />);
    await screen.findByRole("link", { name: "Northwind Payments" });

    fireEvent.change(screen.getByLabelText("Search vendors"), { target: { value: "  CONTOSO " } });
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Contoso Cloud Hosting" })).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 9 vendors")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Search vendors"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "archived" } });
    expect(screen.getByRole("link", { name: /Woodgrove Print/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Northwind Payments" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "active" } });
    fireEvent.change(screen.getByLabelText("Filter by risk"), { target: { value: "unassessed" } });
    expect(screen.getAllByRole("row")).toHaveLength(4); // header + Litware, Proseware, Wingtip
    expect(screen.queryByRole("link", { name: "Tailspin HR Suite" })).not.toBeInTheDocument();
  });

  it("shows an empty-result message with a way to clear the filters", async () => {
    renderApp(<VendorsPage />);
    await screen.findByRole("link", { name: "Northwind Payments" });
    fireEvent.change(screen.getByLabelText("Search vendors"), { target: { value: "zzz" } });
    expect(screen.getByText(/No vendors match these filters/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByRole("link", { name: "Northwind Payments" })).toBeInTheDocument();
  });

  it("explains an empty register", async () => {
    renderApp(<VendorsPage />, { client: createDemoClient({ seed: false }) });
    expect(await screen.findByText("No vendors yet")).toBeInTheDocument();
  });

  it("validates a new vendor before calling the API, then adds it", async () => {
    const client = createDemoClient();
    const create = vi.spyOn(client, "createVendor");
    renderApp(<VendorsPage />, { client });
    await screen.findByRole("link", { name: "Northwind Payments" });

    fireEvent.click(screen.getByRole("button", { name: "Add vendor" }));
    const dialog = screen.getByRole("dialog", { name: "Add vendor" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add vendor" }));
    expect(await within(dialog).findByText("Enter the vendor's name.")).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText("Vendor name"), { target: { value: "  Initech Security  " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add vendor" }));
    expect(await screen.findByRole("link", { name: "Initech Security" })).toBeInTheDocument();
    expect(create).toHaveBeenCalledWith({ name: "Initech Security", status: "active" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows server-side validation and permission errors inside the dialog", async () => {
    const client = createDemoClient();
    client.createVendor = () =>
      Promise.reject(new ApiError(422, "Please correct the highlighted fields.", { fieldErrors: { name: "That name is taken." } }));
    renderApp(<VendorsPage />, { client });
    await screen.findByRole("link", { name: "Northwind Payments" });
    fireEvent.click(screen.getByRole("button", { name: "Add vendor" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Vendor name"), { target: { value: "Dup" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add vendor" }));
    expect(await within(dialog).findByText("That name is taken.")).toBeInTheDocument();

    client.createVendor = () => Promise.reject(new ApiError(403, "Insufficient permissions"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Add vendor" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/role does not allow this action/i);
  });

  it("closes the dialog with Escape", async () => {
    renderApp(<VendorsPage />);
    await screen.findByRole("link", { name: "Northwind Payments" });
    fireEvent.click(screen.getByRole("button", { name: "Add vendor" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("surfaces a load failure", async () => {
    renderApp(<VendorsPage />, { client: failingClient(new ApiError(500, "Internal server error")) });
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });
});

describe("Vendor detail", () => {
  async function open(name: string) {
    const client = createDemoClient();
    const vendor = (await client.listVendors({ limit: 100 })).items.find((v) => v.name === name);
    resetNav({ id: vendor?.id ?? "" });
    renderApp(<VendorDetailPage />, { client });
    return { client, vendor };
  }

  it("shows the vendor, its current risk and its assessments", async () => {
    await open("Tailspin HR Suite");
    expect(await screen.findByRole("heading", { level: 1, name: "Tailspin HR Suite" })).toBeInTheDocument();
    const risk = screen.getByText("Current risk").closest("section") as HTMLElement;
    expect(await within(risk).findByText(/Critical/)).toBeInTheDocument();
    expect(within(risk).getByRole("link", { name: /view the result/i })).toBeInTheDocument();
    expect(screen.getByText("Assessments").closest("section")).toHaveTextContent(/Started/);
  });

  it("edits the name and archives the vendor", async () => {
    const { client, vendor } = await open("Northwind Payments");
    await screen.findByRole("heading", { level: 1, name: "Northwind Payments" });
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Edit vendor" });
    fireEvent.change(within(dialog).getByLabelText("Vendor name"), { target: { value: "Northwind Payments Ltd" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Northwind Payments Ltd" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Restore" })).toBeInTheDocument());
    expect((await client.getVendor(vendor?.id ?? "")).status).toBe("archived");
    expect(screen.getByRole("button", { name: "Start assessment" })).toBeDisabled();
  });

  it("starts an assessment from the vendor page", async () => {
    await open("Proseware Identity");
    await screen.findByRole("heading", { level: 1, name: "Proseware Identity" });
    fireEvent.click(screen.getByRole("button", { name: "Start assessment" }));
    const dialog = await screen.findByRole("dialog", { name: /Assess Proseware Identity/ });
    fireEvent.click(await within(dialog).findByRole("button", { name: "Start assessment" }));
    expect(await within(dialog).findByText("Choose a questionnaire.")).toBeInTheDocument();
    const select = await within(dialog).findByLabelText("Questionnaire");
    fireEvent.change(select, { target: { value: (within(select).getByRole("option", { name: /starter/ }) as HTMLOptionElement).value } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Start assessment" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith(expect.stringMatching(/^\/assessments\/.+/)));
  });

  it("reports a vendor that does not exist", async () => {
    resetNav({ id: "00000000-0000-4000-8000-00000000dead" });
    renderApp(<VendorDetailPage />);
    expect(await screen.findByText("Not found")).toBeInTheDocument();
  });
});
